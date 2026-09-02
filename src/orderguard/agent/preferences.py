"""Deterministic extraction of a stated budget from the user's own words.

Same principle as everywhere else a hard constraint gates the Decision
Council (see ``decision_council.py``): a budget is either positively stated
by the user or it is not, and only code — never an LLM guess — decides
which. This never infers a number the user did not actually say.
"""

from __future__ import annotations

import re

__all__ = ["extract_budget_minor"]

_PATTERN = re.compile(
    r"(?:under|below|less than|not more than|max(?:imum)?(?:\s+of)?|budget(?:\s+of|\s+is)?|within)"
    r"\s*(?:rs\.?|₹|inr)?\s*(\d+(?:\.\d+)?)\s*(?:rs\.?|rupees|inr|₹)?",
    re.IGNORECASE,
)


def extract_budget_minor(text: str) -> int | None:
    """Returns the stated budget in paise, or ``None`` if the user never
    stated one. Never guesses from price-adjacent words like "cheap" or
    "affordable" — those are not a number."""
    match = _PATTERN.search(text)
    if not match:
        return None
    rupees = float(match.group(1))
    if rupees <= 0:
        return None
    return round(rupees * 100)
