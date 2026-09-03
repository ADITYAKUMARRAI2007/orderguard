"""Commerce Missions: the commerce-specific instance of ``lifecycle.py``'s
general action model. A mission decomposes one free-text goal into ordered
sub-intents, each independently risk-governed via ``orchestrator.run_agent_turn``.

**Multi-intent authorization semantics, stated precisely** (an earlier
wording — "each sub-transaction independently authorized" — was corrected
because it could be misread as implying every sub-intent gets some kind of
``Authorization``): every intent is independently risk-governed by this
lifecycle; only a financial sub-transaction is ever OrderGuard-authorized,
and that authorization is always separate per sub-transaction, never merged
into one global spend approval across a mission. An R0 GitHub read produces
an audit event and nothing else — it is never a candidate for
``Authorization`` at all, because ``Authorization`` only exists downstream of
the commerce ``select_offer -> confirm -> gates`` path this module never
touches directly.

Decomposition here is a deterministic keyword classifier, not an LLM call —
this is intentional. A mission's *routing* (which category each clause
belongs to) is exactly the kind of decision this project keeps out of the
probabilistic layer wherever a cheap deterministic answer exists; the LLM
runtime is still what searches and reads within each already-decided
category.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .connector_accounts import ConnectorAccountStore
from .orchestrator import MissionStepResult, run_agent_turn
from .runtime.base import AgentRuntime, ImageInput

__all__ = ["MissionIntent", "MissionResult", "decompose", "decompose_intents", "run_mission"]

_SPLIT = re.compile(r"\band\b|\bthen\b|,", re.IGNORECASE)

_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DEV_TASK", ("github", "issue", "pull request", "pr ", "repo")),
    ("COMMERCE_FOOD", ("dinner", "lunch", "pizza", "food", "restaurant", "order food")),
    ("COMMERCE_GROCERY", ("grocery", "groceries", "milk", "instamart", "vegetables")),
)

# A clause opening with one of these reads as a trailing modifier of
# whatever came right before it ("...and for work address"), not an
# independent request of its own — "buy coffee" needs no preposition to
# stand alone, but "for work address" is not a request at all without the
# clause it's modifying. Matching on this, not merely "no keyword", is what
# keeps a genuinely keyword-less new request (e.g. "buy coffee") standing on
# its own instead of being folded away too.
_MODIFIER_STARTS = ("for ", "to ", "at ", "in ", "with ", "under ", "by ", "using ", "via ", "from ")


def decompose(message: str) -> list[str]:
    """Split into clauses; classify each into a capability category. Falls
    back to COMMERCE_GENERAL for anything that matches no keyword set —
    never silently drops a clause.
    """
    return [intent.capability for intent in decompose_intents(message)]


@dataclass(frozen=True)
class MissionIntent:
    intent_id: str
    text: str
    capability: str
    risk_tier: str = "R0"


def _matches_a_keyword(clause: str) -> bool:
    lowered = clause.lower()
    return any(any(w in lowered for w in words) for _, words in _KEYWORDS)


def _reads_as_a_trailing_modifier(clause: str) -> bool:
    return not _matches_a_keyword(clause) and clause.lower().startswith(_MODIFIER_STARTS)


def decompose_intents(message: str) -> list[MissionIntent]:
    """Retain each clause alongside its deterministic capability decision.

    Keeping the original clause prevents a GitHub tool from receiving the
    commerce half of a mixed mission (and vice versa), which also makes audit
    evidence attributable to one independently governed intent.
    """
    raw_clauses = [c.strip() for c in _SPLIT.split(message) if c.strip()]
    if not raw_clauses:
        raw_clauses = [message.strip()]

    # "and" and "," also show up INSIDE a single request ("milk under 60
    # and for work address"), not just between two separate ones. Real,
    # reproduced incident: "order milk under 60 and for work address and
    # dinner under 500 and for same address" split into FOUR clauses, two
    # of which ("for work address", "for same address") match no keyword on
    # their own and became their own bogus COMMERCE_GENERAL intents asking
    # "what are you ordering?" about an order that was never lost — they
    # were trailing modifiers of the clause right before them. A clause
    # that opens with a preposition AND matches no keyword of its own is
    # folded back into the previous clause instead of spawning a step
    # nothing was actually asking for; a genuinely new, keyword-less
    # request ("buy coffee") does not open that way and still stands alone
    # (see test_decomposition_retains_each_clause_for_independent_routing).
    # Only the very first clause, with nothing to fold into, always stands
    # alone regardless.
    clauses: list[str] = []
    for raw in raw_clauses:
        if clauses and _reads_as_a_trailing_modifier(raw):
            clauses[-1] = f"{clauses[-1]} and {raw}"
        else:
            clauses.append(raw)

    intents: list[MissionIntent] = []
    for clause in clauses:
        lowered = clause.lower()
        matched = next(
            (category for category, words in _KEYWORDS if any(w in lowered for w in words)),
            "COMMERCE_GENERAL",
        )
        intents.append(MissionIntent(
            intent_id=uuid.uuid4().hex,
            text=clause,
            capability=matched,
            risk_tier="R0",
        ))
    return intents


@dataclass
class MissionResult:
    mission_id: str
    message: str
    intents: list[MissionIntent]
    steps: list[MissionStepResult]


async def run_mission(
    *,
    message: str,
    runtime: AgentRuntime,
    accounts: ConnectorAccountStore,
    max_risk_tier: str = "R0",
    continue_category: str | None = None,
    session_context: dict | None = None,
    image: ImageInput | None = None,
) -> MissionResult:
    """``continue_category`` + ``session_context`` are how a reply to an open
    conversation reaches the SAME connector conversation instead of being
    re-decomposed from scratch. Real, reproduced incident: a model asked
    "Which address should I use for delivery?", the user replied "work
    address", and the keyword classifier — which has no memory of the prior
    turn — routed that reply to COMMERCE_GENERAL and found no eligible
    connector, because nothing in "work address" matches a grocery/food
    keyword. When ``continue_category`` is set, the whole message is treated
    as a single intent in that category and the caller's stored
    ``session_context`` (the runtime's own resume state — see
    ``runtime/base.py::AgentTurnResult``) is threaded straight through,
    skipping decomposition entirely.

    ``image`` (an attached shopping-list photo) is passed to every intent's
    turn unchanged — decomposition runs on ``message`` text alone, so a
    typical image-upload request (a short or empty caption) forms one
    intent anyway, and that one turn is expected to make multiple real
    searches itself for the several items an image can contain (see
    prompt.py) rather than this module trying to split the image itself.
    """
    if continue_category:
        intents = [MissionIntent(
            intent_id=uuid.uuid4().hex, text=message,
            capability=continue_category, risk_tier="R0",
        )]
    else:
        intents = decompose_intents(message)
    steps: list[MissionStepResult] = []
    for intent in intents:
        step = await run_agent_turn(
            message=intent.text, category=intent.capability, runtime=runtime,
            accounts=accounts, max_risk_tier=max_risk_tier,
            session_context=session_context if continue_category else None,
            image=image,
        )
        steps.append(step)
    return MissionResult(
        mission_id=uuid.uuid4().hex,
        message=message,
        intents=intents,
        steps=steps,
    )
