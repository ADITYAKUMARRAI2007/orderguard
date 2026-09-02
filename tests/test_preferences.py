"""Deterministic budget extraction — never an LLM guess."""

import pytest

from orderguard.agent.preferences import extract_budget_minor


@pytest.mark.parametrize(
    "text,expected_minor",
    [
        ("order bread under 60 rupees", 6000),
        ("order bread under ₹60", 6000),
        ("get milk below 45 rs", 4500),
        ("bread, budget of 100", 10000),
        ("bread, budget is 100", 10000),
        ("max 75.50 for the bread", 7550),
        ("not more than 200 inr", 20000),
        ("within 30", 3000),
    ],
)
def test_a_stated_budget_is_extracted_in_paise(text, expected_minor):
    assert extract_budget_minor(text) == expected_minor


def test_no_stated_number_means_no_budget():
    assert extract_budget_minor("order the cheapest bread you can find") is None
    assert extract_budget_minor("order bread from instamart") is None


def test_a_zero_or_negative_budget_is_not_a_real_constraint():
    assert extract_budget_minor("under 0 rupees") is None
