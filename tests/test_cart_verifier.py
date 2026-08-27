"""Tests for the deterministic cart read-back check."""

import pytest
from pydantic import ValidationError

from orderguard.cart_verifier import (
    ApprovedCartLine,
    CartExpectation,
    cart_hash,
    compare_cart,
)
from orderguard.models import CartLine, ObservedCart


def _expected(**changes) -> CartExpectation:
    data = {
        "merchant": "shop.example",
        "currency": "INR",
        "maximum_total_paise": 50000,
        "lines": [ApprovedCartLine(variant_id="banana", quantity=6, unit_price_paise=1200)],
    }
    data.update(changes)
    return CartExpectation(**data)


def _cart(**changes) -> ObservedCart:
    data = {
        "merchant": "shop.example",
        "cart_id": "cart-1",
        "currency": "INR",
        "lines": [
            CartLine(
                sku="banana", variant_id="banana", quantity=6,
                unit_price_paise=1200,
            )
        ],
        "total_paise": 7200,
    }
    data.update(changes)
    return ObservedCart(**data)


def test_exact_cart_matches():
    result = compare_cart(_expected(), _cart())
    assert result.matches
    assert result.within_cap
    assert len(result.cart_hash) == 64


def test_sixty_bananas_are_blocked_when_six_were_approved():
    cart = _cart(lines=[CartLine(sku="banana", variant_id="banana", quantity=60, unit_price_paise=1200)])
    result = compare_cart(_expected(), cart)
    assert not result.matches
    assert not result.matches_quantities
    assert "60" in result.failures[0]


def test_an_extra_unapproved_variant_is_blocked():
    cart = _cart(lines=[
        CartLine(sku="banana", variant_id="banana", quantity=6, unit_price_paise=1200),
        CartLine(sku="milk", variant_id="milk", quantity=1, unit_price_paise=6600),
    ])
    result = compare_cart(_expected(), cart)
    assert not result.matches
    assert not result.matches_quantities


def test_over_cap_is_blocked_even_when_quantities_match():
    result = compare_cart(_expected(maximum_total_paise=5000), _cart())
    assert not result.matches
    assert result.matches_quantities
    assert not result.within_cap


def test_wrong_merchant_and_currency_are_blocked():
    result = compare_cart(_expected(), _cart(merchant="other.example", currency="USD"))
    assert not result.matches
    assert not result.matches_merchant
    assert not result.matches_currency


def test_cart_hash_changes_when_the_cart_changes():
    original = _cart()
    changed = _cart(lines=[CartLine(sku="banana", variant_id="banana", quantity=7, unit_price_paise=1200)])
    assert cart_hash(original) != cart_hash(changed)


def test_duplicate_approved_variant_is_refused_at_the_boundary():
    with pytest.raises(ValidationError, match="same variant twice"):
        _expected(lines=[
            ApprovedCartLine(variant_id="banana", quantity=2, unit_price_paise=1200),
            ApprovedCartLine(variant_id="banana", quantity=4, unit_price_paise=1200),
        ])


# --- the price the user was shown ------------------------------------------

def test_a_silent_price_rise_under_the_cap_is_blocked():
    """The bait and switch: right shop, right item, right count, wrong price.

    The user chose an offer quoted at ₹12 a banana — ₹72 for six. Between the
    search and the cart write the merchant charges ₹80 each instead: ₹480.
    That is still under the ₹500 cap, so the cap does not catch it, and every
    other check is about identity rather than money.

    A cap is a ceiling, not a price check. What the user actually approved was
    a *quoted price*, and that has to be enforced on its own.
    """
    expected = _expected(
        lines=[ApprovedCartLine(variant_id="banana", quantity=6, unit_price_paise=1200)]
    )
    overcharged = _cart(
        lines=[
            CartLine(sku="banana", variant_id="banana", quantity=6, unit_price_paise=8000)
        ],
        total_paise=48000,
    )

    result = compare_cart(expected, overcharged)

    assert result.within_cap          # the cap is happy: ₹480 < ₹500
    assert result.matches_quantities  # six bananas, exactly as asked
    assert not result.matches_prices  # and yet
    assert not result.matches


def test_price_check_works_on_a_line_with_no_unit_price():
    """Real Shopify carts quote a line total and no per-unit price.

    An earlier version of the price check compared ``unit_price_paise``, which
    is optional and was ``None`` on every live cart line. The comparison then
    failed against ``[None]`` and blocked a perfectly good cart — a false block
    on the happy path, which is how a safety check gets switched off.

    The check is on line totals, which the model guarantees are populated.
    """
    quoted = _expected(
        lines=[ApprovedCartLine(variant_id="v1", quantity=2, unit_price_paise=9405)]
    )
    live_shape = _cart(
        lines=[CartLine(sku="v1", variant_id="v1", quantity=2, line_total_paise=18810)],
        total_paise=18810,
    )

    result = compare_cart(quoted, live_shape)

    assert live_shape.lines[0].unit_price_paise is None   # exactly as Shopify sends it
    assert result.matches_prices
    assert result.matches


def test_an_indivisible_line_total_is_still_checked_exactly():
    """A discount across three units gives a total that does not divide evenly.

    100 paise over 3 units is 33.33 each. Dividing to get a unit price would
    invent a number and then compare against the invention. Multiplying the
    approved price up instead keeps the arithmetic exact.
    """
    quoted = _expected(
        lines=[ApprovedCartLine(variant_id="v1", quantity=3, unit_price_paise=100)]
    )
    discounted = _cart(
        lines=[CartLine(sku="v1", variant_id="v1", quantity=3, line_total_paise=299)],
        total_paise=299,
    )

    result = compare_cart(quoted, discounted)

    # 299 != 300. It is one paise in the shopper's favour and it still stops,
    # because the cart no longer matches what they approved. Stopping to ask is
    # the correct behaviour for a payment system; see D-024's stated limitation.
    assert not result.matches_prices
