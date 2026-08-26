"""Tests for money handling.

The point of these tests: prove that no rupee amount can silently become a
slightly different rupee amount.
"""

from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from orderguard.money import MoneyError, format_rupees, to_paise, to_rupees


# --- the normal cases -------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("24.99"), 2499),
        (Decimal("0.01"), 1),
        (Decimal("0"), 0),
        (Decimal("500"), 50000),
        (Decimal("1.5"), 150),      # one decimal place is fine
        ("24.99", 2499),            # strings are accepted
        ("500", 50000),
        (299, 29900),               # plain ints are rupees
        (0, 0),
    ],
)
def test_converts_rupees_to_paise(value, expected):
    assert to_paise(value) == expected


def test_converts_paise_back_to_rupees():
    assert to_rupees(2499) == Decimal("24.99")
    assert to_rupees(0) == Decimal("0.00")
    assert to_rupees(1) == Decimal("0.01")


def test_formats_for_humans():
    assert format_rupees(2499) == "₹24.99"
    assert format_rupees(50000) == "₹500.00"


# --- the refusals: these are the tests that matter --------------------------

def test_float_is_refused():
    """A float for money is an error, not a warning.

    This is the single most important test in the file. Floats are how
    reconciliation systems quietly lose money.
    """
    with pytest.raises(MoneyError, match="float is not allowed"):
        to_paise(24.99)


def test_float_zero_is_also_refused():
    """0.0 is still a float. No exceptions to the rule."""
    with pytest.raises(MoneyError):
        to_paise(0.0)


def test_bool_is_refused():
    """In Python, True == 1. Without this guard, True would become ₹1.00."""
    with pytest.raises(MoneyError):
        to_paise(True)


def test_more_than_two_decimals_is_refused():
    """₹1.005 is not a real amount. Rounding it would invent or destroy money."""
    with pytest.raises(MoneyError, match="more than 2 decimal places"):
        to_paise(Decimal("1.005"))


def test_nonsense_is_refused():
    with pytest.raises(MoneyError):
        to_paise("not a number")


def test_infinity_is_refused():
    with pytest.raises(MoneyError):
        to_paise(Decimal("Infinity"))


def test_paise_must_be_int():
    with pytest.raises(MoneyError):
        to_rupees(Decimal("24.99"))     # already rupees, wrong direction


# --- property tests: true for ANY value, not just the ones I thought of -----

@given(st.integers(min_value=0, max_value=10**11))
def test_round_trip_never_loses_a_paisa(paise):
    """paise -> rupees -> paise must return exactly what we started with."""
    assert to_paise(to_rupees(paise)) == paise


@given(st.integers(min_value=0, max_value=10**9))
def test_whole_rupees_always_end_in_double_zero(rupees):
    assert to_paise(rupees) % 100 == 0


@given(st.floats(allow_nan=True, allow_infinity=True))
def test_no_float_ever_gets_through(value):
    """Whatever the float, it is refused. No special cases."""
    with pytest.raises(MoneyError):
        to_paise(value)
