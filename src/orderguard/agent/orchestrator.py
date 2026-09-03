"""Ties the agent package together: eligible connector -> runtime turn ->
normalized ``ConnectorResult`` -> (for commerce) the existing, unmodified
Decision Council. Everything before Decision Council is new; Decision
Council itself, and everything after it (select/confirm/gates/Authorization/
payment), is untouched.

OrderGuard's own MCP tools (``record_intent``/``check_cart``/etc.) are called
in-process as plain Python functions here, not round-tripped through the
``/mcp`` HTTP endpoint — this orchestrator already runs inside the same
backend process. ``/mcp`` stays exactly as it is for *external* agents
(Claude Code, Claude Desktop) to keep using.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from ..commerce.stores import for_query
from ..decision_council import run_decision_council
from ..llm import provider_from_env
from .connector_accounts import ConnectorAccountStore
from .connector_registry import RegisteredConnector, tools_within_ceiling
from .eligibility import ConnectorEligibilityEngine
from .normalizer import normalize
from .preferences import extract_budget_minor
from .prompt import SYSTEM_PROMPT
from .results import CommerceResult, ConnectorResult
from .runtime.base import AgentRuntime, ImageInput
from .tools import ConnectorInvocationSpec, FinancialToolExposureError

__all__ = [
    "MissionStepResult", "run_agent_turn", "R3ExposureBlocked",
    "IneligibleConnectorSelectionError", "ConnectorProvenanceError",
]


class R3ExposureBlocked(RuntimeError):
    """Surfaced to the API layer as a 4xx, after the caller has already
    emitted ``r3_tool_exposure_blocked`` to the audit chain — see
    ``app.py``'s ``/api/agent/run`` handler."""


class IneligibleConnectorSelectionError(RuntimeError):
    """The runtime reported a connector/tool not present in its eligible set."""


class ConnectorProvenanceError(RuntimeError):
    """A result's canonical connector/server/resource mapping did not match."""


# Real, live-found gap (2026-09-03, see FAILURE_LOG.md): a mission's
# category is decided deterministically from the TYPED text alone, before
# any image attached to the same turn is ever read (missions.py's
# classifier never inspects image bytes -- see that module's own
# docstring on why routing stays out of the probabilistic layer). A photo
# of a grocery list with a generic caption ("check this out", "add to
# cart") carries none of missions.py's COMMERCE_GROCERY keywords in its
# own text, so it falls to COMMERCE_GENERAL and only ever reaches
# Shopify's non-grocery demo stores -- never Swiggy Instamart, even though
# it is connected and is exactly the right connector for what the photo
# actually shows. Deterministic keyword routing cannot read the image
# (that would require an LLM call before eligibility is even decided,
# which this project deliberately avoids), so the fix widens WHICH
# connectors an image-attached fallback turn can reach instead of trying
# to guess the category from the image up front: the model itself, given
# the real image and real search tools for more than one category, is
# what decides which connector's results actually answer the request.
_IMAGE_FALLBACK_EXTRA_CATEGORIES: dict[str, tuple[str, ...]] = {
    "COMMERCE_GENERAL": ("COMMERCE_GROCERY",),
}


@dataclass
class MissionStepResult:
    category: str
    connector_id: str | None
    results: list[ConnectorResult]
    council: object | None  # CouncilResult, only set for commerce categories
    # The model's own free-text turn output and how long the runtime call
    # took. Both existed on AgentTurnResult already but were computed and
    # discarded — a real transparency gap: a step with zero results (the
    # model made no tool call, or asked a clarifying question) looked
    # identical in the UI to "nothing happened," with no way to see what
    # the model actually said or how long it took to say it.
    model_text: str = ""
    duration_ms: int = 0
    # The budget this step actually used, in paise — None when the user
    # never stated one this turn. Surfaced to the UI so it can show, truthfully,
    # whether personalization had anything to work with (see preferences.py).
    budget_minor: int | None = None
    # Connectors that WERE eligible for this category, whether or not the
    # runtime actually called one. Without this, a turn where the model asks
    # a clarifying question before making any tool call is indistinguishable
    # from "no connector was eligible at all" — connector_id is None either
    # way, and the UI has no honest way to tell "not authenticated" apart
    # from "authenticated, just hasn't been called yet this turn."
    eligible_connector_ids: list[str] = field(default_factory=list)
    # Real, live-found gap (2026-09-03, see FAILURE_LOG.md F-041): with more
    # than one eligible connector in a turn, the model's own text is not a
    # trustworthy record of which ones it actually searched — a live,
    # reproduced case had it claim a fully-connected, working connector was
    # "disconnected mid-session" when it had simply never called it. Built
    # from the runtime's own real tool_calls (the same evidence
    # ``connector_results`` is built from), never from what the model says
    # about itself, so the UI/audit has ground truth independent of the
    # model's own narration.
    attempted_connector_ids: list[str] = field(default_factory=list)
    # Opaque continuation state for whichever runtime handled this step —
    # see runtime/base.py::AgentTurnResult. The caller (missions.py/app.py)
    # persists this and passes it back on the NEXT call in the same
    # conversation; this module never inspects its shape.
    session_context: dict = field(default_factory=dict)


def _build_spec(
    connector: RegisteredConnector, accounts: ConnectorAccountStore,
    max_risk_tier: str,
) -> ConnectorInvocationSpec:
    tools = tools_within_ceiling(connector, max_risk_tier)
    if connector.id == "shopify":
        # Resolved per-call: the registry entry has no fixed URL because
        # "Shopify" is many independently verified stores, not one server.
        raise ValueError("shopify URL must be resolved by the caller before building a spec")
    bearer_token = accounts.bearer_token(connector.id) if connector.auth == "connector_account" else None
    return ConnectorInvocationSpec(
        connector_id=connector.id, url=connector.url, tools=tools, bearer_token=bearer_token,
    )


async def run_agent_turn(
    *,
    message: str,
    category: str,
    runtime: AgentRuntime,
    accounts: ConnectorAccountStore,
    max_risk_tier: str = "R0",
    session_context: dict | None = None,
    image: ImageInput | None = None,
    image_context_established: bool = False,
) -> MissionStepResult:
    """One capability's worth of the mission: eligible connector(s) in
    ``category`` are offered to ``runtime``; whatever tool calls it makes are
    normalized and, for commerce, ranked by the existing Decision Council.
    External connector credentials always come from the owner-scoped
    ``ConnectorAccountStore``.

    ``image_context_established`` is True when an EARLIER turn in this same
    conversation thread attached an image, even though THIS turn's own
    ``image`` is None -- see FAILURE_LOG.md F-042. Without it, the widened
    connector set an image turn established silently narrows back down on
    every continuation reply, which the model (correctly observing its own
    tool list shrink mid-conversation) then narrates as a connector having
    failed, when nothing failed at all.
    """
    engine = ConnectorEligibilityEngine(accounts)
    eligible = engine.eligible_for(
        category, runtime_name=runtime.name,
        owner_ref=accounts.owner_ref, max_risk_tier=max_risk_tier,
    )
    if image is not None or image_context_established:
        seen_ids = {connector.id for connector in eligible}
        for extra_category in _IMAGE_FALLBACK_EXTRA_CATEGORIES.get(category, ()):
            for connector in engine.eligible_for(
                extra_category, runtime_name=runtime.name,
                owner_ref=accounts.owner_ref, max_risk_tier=max_risk_tier,
            ):
                if connector.id not in seen_ids:
                    eligible.append(connector)
                    seen_ids.add(connector.id)

    specs: list[ConnectorInvocationSpec] = []
    for connector in eligible:
        if connector.id == "shopify":
            stores = for_query(message)
            if not stores:
                continue
            # One canonical connector, many independently eligible merchant
            # surfaces. Unique server names keep both native MCP wire formats
            # valid without collapsing the candidate set before the Council.
            for index, store in enumerate(stores):
                specs.append(ConnectorInvocationSpec(
                    connector_id="shopify",
                    server_name=f"shopify-{index}",
                    resource_ref=store.domain,
                    url=f"https://{store.domain}/api/mcp",
                    tools=tools_within_ceiling(connector, max_risk_tier),
                    bearer_token=None,
                ))
        else:
            specs.append(_build_spec(connector, accounts, max_risk_tier))

    if not specs:
        return MissionStepResult(category=category, connector_id=None, results=[], council=None)

    turn_started = time.monotonic()
    turn = await runtime.run_turn(SYSTEM_PROMPT, message, specs, session_context=session_context, image=image)
    duration_ms = round((time.monotonic() - turn_started) * 1000)

    # Extracted from the user's own words, deterministically — never an LLM
    # guess — same "positively stated or it doesn't count" rule the Council
    # already applies to every other hard constraint. Without a stated
    # budget, within_budget stays None for every offer, filter_eligible
    # drops all of them, and the Council can never recommend anything; this
    # is what actually lets it produce a real, personalized pick.
    budget_minor = extract_budget_minor(message)

    spec_by_server = {s.server_name or s.connector_id: s for s in specs}
    connector_by_id = {c.id: c for c in eligible}
    connector_results: list[ConnectorResult] = []
    chosen_connector_id: str | None = None
    attempted_connector_ids: list[str] = []
    for call in turn.tool_calls:
        server_name = call.server_name or call.connector_id
        spec = spec_by_server.get(server_name)
        if spec is None:
            raise IneligibleConnectorSelectionError(
                f"runtime selected ineligible connector/server {server_name!r}"
            )
        if call.connector_id != spec.connector_id:
            raise ConnectorProvenanceError(
                f"result claimed connector {call.connector_id!r} but server "
                f"{server_name!r} belongs to {spec.connector_id!r}"
            )
        if call.resource_ref not in (None, spec.resource_ref):
            raise ConnectorProvenanceError(
                f"result resource {call.resource_ref!r} did not match eligible "
                f"resource {spec.resource_ref!r}"
            )
        allowed = {tool.name: tool for tool in spec.tools}
        if call.tool_name not in allowed:
            raise IneligibleConnectorSelectionError(
                f"runtime selected ineligible tool {call.tool_name!r} on {server_name!r}"
            )
        # The runtime adapter owns this mapping. Do not trust model-returned
        # arguments to state which merchant/server produced a result.
        call.resource_ref = spec.resource_ref
        connector = connector_by_id[spec.connector_id]
        risk_tier = allowed[call.tool_name].risk_tier
        chosen_connector_id = spec.connector_id
        if spec.connector_id not in attempted_connector_ids:
            attempted_connector_ids.append(spec.connector_id)
        result = normalize(
            call,
            capability=category,
            risk_tier=risk_tier,
            provenance=(
                f"{runtime.name}:{server_name}"
                + (f":{spec.resource_ref}" if spec.resource_ref else "")
            ),
            budget_minor=budget_minor,
        )
        # None means the call succeeded but was purely informational (e.g.
        # Swiggy's mandatory get_addresses lookup before a search) — nothing
        # offer-shaped to report, not a failure to surface. See
        # normalizer.py::normalize's own docstring.
        if result is not None:
            connector_results.append(result)

    council = None
    commerce_offers = [
        offer
        for r in connector_results
        if isinstance(r.payload, CommerceResult)
        for offer in r.payload.offers
    ]
    if commerce_offers:
        council = run_decision_council(commerce_offers, provider_from_env())

    return MissionStepResult(
        category=category, connector_id=chosen_connector_id,
        results=connector_results, council=council,
        model_text=turn.text, duration_ms=duration_ms,
        session_context=turn.session_context,
        budget_minor=budget_minor,
        eligible_connector_ids=[c.id for c in eligible],
        attempted_connector_ids=attempted_connector_ids,
    )


def new_execution_id() -> str:
    return uuid.uuid4().hex
