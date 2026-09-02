"""The council recommends; deterministic code has the only vote.

Every test either proves the hard filter is genuinely a filter (not a sort),
or proves the code veto actually fires when an agent is rigged to misbehave —
the two properties this module exists for.
"""

import pytest

from orderguard.commerce.base import Offer
from orderguard.commerce.search import ScoredOffer
from orderguard.decision_council import filter_eligible, run_decision_council
from orderguard.llm import StubProvider


def _offer(store="a.example", variant="v1", price=1000, title="Black Running Shoe",
           in_stock=True, priced=True, within_budget=True, relevance=1.0) -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            store=store, store_label=store.split(".")[0].title(),
            product_id=f"p-{variant}", variant_id=variant, title=title,
            price_minor=price, currency="INR", available=in_stock,
        ),
        relevance=relevance, in_stock=in_stock, priced=priced,
        within_budget=within_budget, line_total_minor=price,
    )


# --- the filter is a hard drop, not a sort ----------------------------------

def test_out_of_stock_is_dropped_not_deprioritised():
    offers = [_offer(variant="v1", in_stock=False), _offer(variant="v2")]
    eligible = filter_eligible(offers)
    assert [e.candidate_id for e in eligible] == ["a.example|v2"]


def test_over_budget_is_dropped():
    offers = [_offer(variant="v1", within_budget=False), _offer(variant="v2")]
    eligible = filter_eligible(offers)
    assert [e.candidate_id for e in eligible] == ["a.example|v2"]


def test_unresolved_budget_is_excluded_not_passed_through():
    """The correction that matters: within_budget=None means UNRESOLVED, and a
    hard constraint must be positively satisfied — None is not 'not proven
    false', it is 'not proven at all', and gets treated the same as False."""
    offers = [_offer(variant="v1", within_budget=None), _offer(variant="v2")]
    eligible = filter_eligible(offers)
    assert [e.candidate_id for e in eligible] == ["a.example|v2"]


def test_unpriced_is_dropped():
    offers = [_offer(variant="v1", priced=False), _offer(variant="v2")]
    eligible = filter_eligible(offers)
    assert [e.candidate_id for e in eligible] == ["a.example|v2"]


# --- trivial cases need no LLM at all ---------------------------------------

def test_nothing_eligible_recommends_nothing():
    result = run_decision_council([_offer(in_stock=False)], StubProvider())
    assert result.recommended_id is None
    assert result.fallback_used is False
    assert result.alternatives_rejected == 1


def test_exactly_one_eligible_is_recommended_without_calling_the_model():
    result = run_decision_council([_offer()], StubProvider())
    assert result.recommended_id == "a.example|v1"
    assert result.fallback_used is False
    assert result.alternatives_considered == 1


# --- the real case: two eligible candidates, the council reasons -----------

def _two_candidates():
    return [
        _offer(store="a.example", variant="v1", price=4799, relevance=0.9),
        _offer(store="b.example", variant="v2", price=4899, relevance=1.0),
    ]


def test_fit_agent_recommendation_is_used_when_the_critic_approves():
    summary = (
        "a.example|v1: 'Black Running Shoe' from A, total 4799 minor units, relevance 0.9\n"
        "b.example|v2: 'Black Running Shoe' from B, total 4899 minor units, relevance 1.0"
    )
    stub = StubProvider(extra_answers={
        f"Eligible candidates:\n{summary}\n\nWhich fits best?": {
            "candidate_id": "b.example|v2", "rationale": "Best title match.",
        },
        f"Eligible candidates:\n{summary}\n\n"
        f"Proposed recommendation: b.example|v2\nReview it.": {
            "verdict": "APPROVE", "candidate_id": "b.example|v2",
            "rationale": "Reasonable given the relevance gap.",
        },
    })
    result = run_decision_council(_two_candidates(), stub)
    assert result.recommended_id == "b.example|v2"
    assert result.fallback_used is False
    assert result.alternatives_considered == 2


def test_critic_challenge_overrides_the_fit_pick_when_valid():
    summary = (
        "a.example|v1: 'Black Running Shoe' from A, total 4799 minor units, relevance 0.9\n"
        "b.example|v2: 'Black Running Shoe' from B, total 4899 minor units, relevance 1.0"
    )
    stub = StubProvider(extra_answers={
        f"Eligible candidates:\n{summary}\n\nWhich fits best?": {
            "candidate_id": "b.example|v2", "rationale": "Slightly higher relevance.",
        },
        f"Eligible candidates:\n{summary}\n\n"
        f"Proposed recommendation: b.example|v2\nReview it.": {
            "verdict": "CHALLENGE", "candidate_id": "a.example|v1",
            "rationale": "A is cheaper for a negligible relevance difference.",
        },
    })
    result = run_decision_council(_two_candidates(), stub)
    assert result.recommended_id == "a.example|v1"
    assert result.fallback_used is False


# --- the code veto: the property this module exists for --------------------

class _HallucinatingFit:
    """Returns a candidate_id that was never in the eligible set."""

    name = "hallucinating"

    def complete(self, system, user, schema):
        return {"candidate_id": "totally-invented|v999", "rationale": "made up"}


def test_code_veto_fires_when_fit_hallucinates_an_out_of_set_id():
    result = run_decision_council(_two_candidates(), _HallucinatingFit())
    assert result.recommended_id == "a.example|v1"   # the deterministic top pick
    assert result.fallback_used is True
    assert "top-ranked" in result.rationale


class _HallucinatingCritic:
    """Fit behaves; critic challenges with a candidate that doesn't exist."""

    name = "hallucinating-critic"

    def complete(self, system, user, schema):
        if "Review it" in user:
            return {
                "verdict": "CHALLENGE", "candidate_id": "totally-invented|v999",
                "rationale": "made up",
            }
        return {"candidate_id": "b.example|v2", "rationale": "fine"}


def test_code_veto_fires_when_critic_challenges_with_an_out_of_set_id():
    result = run_decision_council(_two_candidates(), _HallucinatingCritic())
    assert result.recommended_id == "a.example|v1"   # fallback, not the invented id
    assert result.fallback_used is True


class _UnavailableProvider:
    name = "down"

    def complete(self, system, user, schema):
        from orderguard.llm import LLMUnavailable
        raise LLMUnavailable("the model is down")


def test_an_unavailable_model_still_produces_a_safe_recommendation():
    """AI outage must not kill the flow — the deterministic fallback stands in."""
    result = run_decision_council(_two_candidates(), _UnavailableProvider())
    assert result.recommended_id == "a.example|v1"
    assert result.fallback_used is True


class _ApprovingCriticOnly:
    """Fit is fine; critic call itself is unavailable."""

    name = "critic-down"

    def complete(self, system, user, schema):
        from orderguard.llm import LLMUnavailable
        if "Review it" in user:
            raise LLMUnavailable("critic down")
        return {"candidate_id": "b.example|v2", "rationale": "good fit"}


def test_fit_pick_stands_when_only_the_critic_is_unavailable():
    result = run_decision_council(_two_candidates(), _ApprovingCriticOnly())
    assert result.recommended_id == "b.example|v2"
    assert result.fallback_used is False


def test_result_always_reports_how_many_alternatives_existed():
    offers = [_offer(variant="v1"), _offer(variant="v2", in_stock=False), _offer(variant="v3")]
    result = run_decision_council(offers, StubProvider())
    assert result.alternatives_rejected == 1
