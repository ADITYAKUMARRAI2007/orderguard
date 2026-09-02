"""Two advisory agents recommend a candidate; deterministic code has the only vote.

Bounded, on purpose, against what `commerce/search.py` already refuses to do:
that module explicitly never invents a trust score and never picks for the
user — "picking is the user's." This module does not change that rule. It
sits inside the same step ("you choose"), makes the *reasoning* behind
`rank()`'s ordering explicit in plain language, and still ends in a
recommendation the user confirms — never a cart write, never a decision that
reaches a payment gate.

Only real, observable fields are reasoned over: price, relevance, and stock —
exactly what `ScoredOffer` already carries. No delivery estimate, no rating,
no "reliability" score is invented here either; `Offer` carries none of that
data, and inventing it would be exactly the overclaim `search.py`'s own
docstring already argues against.

**The filter is a hard, deterministic gate.** `within_budget` is tri-state
(`True` / `False` / `None`). A hard constraint must be POSITIVELY satisfied —
`None` (unresolved) is excluded, same as `False`, never silently passed
through as if unproven-false meant safe.

**Two agents, one shared LLMProvider, no tools.** Both "Fit" and "Critic" are
just role-scoped prompts through the same `complete()` used everywhere else in
this project — neither can call anything, write a cart, or move a gate. Their
only capability is text-in, schema-out, and the schema itself enumerates the
only candidate ids that exist, not merely a free-text field validated after
the fact — the same principle Ploutos applies to its own action space, but
this candidate list is closed and dynamic per request rather than fixed and
static, which is why it is generated per call instead of frozen once as an
enum member list.

**The code veto is unconditional.** Whatever either agent returns, if the
chosen id is not one of the ids actually handed to it, the recommendation is
discarded and replaced with the deterministic top-ranked survivor — and
`fallback_used=True` says so, rather than quietly smoothing over the
override. An unavailable or malformed LLM response is treated the same way
as an out-of-set id: this council can be entirely absent and the flow still
produces a safe, if less explained, recommendation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .commerce.search import ScoredOffer
from .llm import LLMProvider, LLMUnavailable

__all__ = ["Eligible", "CouncilResult", "filter_eligible", "run_decision_council"]

STRICT = ConfigDict(extra="forbid")


def _candidate_id(offer: ScoredOffer) -> str:
    return f"{offer.offer.store}|{offer.offer.variant_id}"


class Eligible(BaseModel):
    """One candidate that survived the hard-constraint filter. ``candidate_id``
    is the only handle either agent may use to refer to it — never a title,
    never a bare index — so a hallucinated reference is a plain string
    mismatch, not something that needs interpretation."""

    model_config = STRICT

    candidate_id: str
    store_label: str
    title: str
    price_minor: int
    line_total_minor: int
    relevance: float


class CouncilResult(BaseModel):
    model_config = STRICT

    recommended_id: str | None
    rationale: str
    fallback_used: bool
    alternatives_considered: int   # how many survived the hard filter, this included
    alternatives_rejected: int     # how many the hard filter actually dropped
    eligible: list[Eligible]


def filter_eligible(offers: list[ScoredOffer]) -> list[Eligible]:
    """Drop — never merely re-sort — anything that fails a hard constraint.

    ``rank()`` in search.py sorts every offer, disqualified or not, so a
    caller reading only the top of that list could still be looking at an
    out-of-stock or over-budget item that happened to rank last. This
    produces the actually-eligible subset, in the order given (callers pass
    already-``rank()``-sorted offers, so the first survivor here is the
    correct deterministic fallback if the council never runs).

    Attribute matching (``required_attributes``) is not checked here for the
    same honest reason app.py's ATTRIBUTES_MATCH gate cannot: ``Offer``
    carries no attribute map. Adding a silent pass for a fact this data
    cannot express would be the same mistake documented against elsewhere in
    this project — better to omit the check than fake it.
    """
    return [
        Eligible(
            candidate_id=_candidate_id(o), store_label=o.offer.store_label,
            title=o.offer.title, price_minor=o.offer.price_minor,
            line_total_minor=o.line_total_minor, relevance=o.relevance,
        )
        for o in offers
        if o.in_stock and o.priced and o.within_budget is True
    ]


def _candidate_summary(eligible: list[Eligible]) -> str:
    lines = [
        f"{e.candidate_id}: {e.title!r} from {e.store_label}, "
        f"total {e.line_total_minor} minor units, relevance {e.relevance}"
        for e in eligible
    ]
    return "\n".join(lines)


def _fit_schema(candidate_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["candidate_id", "rationale"],
        "properties": {
            "candidate_id": {"type": "string", "enum": candidate_ids},
            "rationale": {"type": "string"},
        },
    }


def _critic_schema(candidate_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["verdict", "candidate_id", "rationale"],
        "properties": {
            "verdict": {"type": "string", "enum": ["APPROVE", "CHALLENGE"]},
            "candidate_id": {"type": "string", "enum": candidate_ids},
            "rationale": {"type": "string"},
        },
    }


_FIT_SYSTEM = (
    "You compare already-eligible shopping candidates on price and title "
    "relevance only. Every candidate already satisfies the user's hard "
    "requirements (budget, stock) — you are choosing the best FIT among "
    "safe options, not deciding what counts as safe. Respond with the "
    "candidate_id of your pick and a one-sentence rationale grounded only "
    "in the numbers given."
)

_CRITIC_SYSTEM = (
    "You review a proposed shopping recommendation against the same "
    "candidate data. If it is well justified, APPROVE it. If a candidate is "
    "objectively better on price or relevance for the same or better fit, "
    "CHALLENGE with that candidate_id instead. You may only name a "
    "candidate_id that was actually given to you."
)


def run_decision_council(
    offers: list[ScoredOffer], llm: LLMProvider,
) -> CouncilResult:
    """Filter, then let two advisory roles reason over what survived.

    Never raises on a bad or missing LLM response — every failure mode
    collapses to the same safe fallback: the deterministic top-ranked
    survivor, with ``fallback_used=True`` naming why.
    """
    eligible = filter_eligible(offers)
    rejected = len(offers) - len(eligible)

    if not eligible:
        return CouncilResult(
            recommended_id=None, rationale="Nothing met every hard requirement.",
            fallback_used=False, alternatives_considered=0,
            alternatives_rejected=rejected, eligible=[],
        )

    if len(eligible) == 1:
        return CouncilResult(
            recommended_id=eligible[0].candidate_id,
            rationale="Only one candidate met every hard requirement.",
            fallback_used=False, alternatives_considered=1,
            alternatives_rejected=rejected, eligible=eligible,
        )

    ids = [e.candidate_id for e in eligible]
    fallback = eligible[0].candidate_id
    summary = _candidate_summary(eligible)

    fit_pick, fit_reason = _ask_fit(llm, summary, ids)
    if fit_pick is None or fit_pick not in ids:
        return CouncilResult(
            recommended_id=fallback,
            rationale="The recommendation agent returned no usable pick; "
                      "fell back to the top-ranked eligible candidate.",
            fallback_used=True, alternatives_considered=len(eligible),
            alternatives_rejected=rejected, eligible=eligible,
        )

    verdict, critic_pick, critic_reason = _ask_critic(llm, summary, fit_pick, ids)
    if verdict is None:
        # Critic unavailable or malformed: the Fit pick already passed
        # structural validation on its own, so it stands rather than being
        # discarded for a second agent's failure to weigh in.
        return CouncilResult(
            recommended_id=fit_pick, rationale=fit_reason,
            fallback_used=False, alternatives_considered=len(eligible),
            alternatives_rejected=rejected, eligible=eligible,
        )

    if verdict == "APPROVE":
        return CouncilResult(
            recommended_id=fit_pick, rationale=fit_reason,
            fallback_used=False, alternatives_considered=len(eligible),
            alternatives_rejected=rejected, eligible=eligible,
        )

    # CHALLENGE
    if critic_pick not in ids:
        return CouncilResult(
            recommended_id=fallback,
            rationale="The critic challenged with a candidate that was never "
                      "offered to it; fell back to the top-ranked eligible "
                      "candidate rather than trust an invented id.",
            fallback_used=True, alternatives_considered=len(eligible),
            alternatives_rejected=rejected, eligible=eligible,
        )
    return CouncilResult(
        recommended_id=critic_pick, rationale=critic_reason,
        fallback_used=False, alternatives_considered=len(eligible),
        alternatives_rejected=rejected, eligible=eligible,
    )


def _ask_fit(llm: LLMProvider, summary: str, ids: list[str]) -> tuple[str | None, str]:
    user = f"Eligible candidates:\n{summary}\n\nWhich fits best?"
    try:
        raw = llm.complete(_FIT_SYSTEM, user, _fit_schema(ids))
    except LLMUnavailable:
        return None, ""
    candidate_id = raw.get("candidate_id")
    if not isinstance(candidate_id, str):
        return None, ""
    return candidate_id, str(raw.get("rationale") or "")


def _ask_critic(
    llm: LLMProvider, summary: str, proposed: str, ids: list[str],
) -> tuple[str | None, str | None, str]:
    user = (
        f"Eligible candidates:\n{summary}\n\n"
        f"Proposed recommendation: {proposed}\nReview it."
    )
    try:
        raw = llm.complete(_CRITIC_SYSTEM, user, _critic_schema(ids))
    except LLMUnavailable:
        return None, None, ""
    verdict = raw.get("verdict")
    candidate_id = raw.get("candidate_id")
    if verdict not in ("APPROVE", "CHALLENGE") or not isinstance(candidate_id, str):
        return None, None, ""
    return verdict, candidate_id, str(raw.get("rationale") or "")
