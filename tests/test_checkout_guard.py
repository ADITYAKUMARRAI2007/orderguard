"""A payment can be considered only after deterministic confirmation and gates."""

from orderguard.cart_verifier import ApprovedCartLine, CartExpectation
from orderguard.checkout_guard import (
    CheckoutEvidence,
    confirm_cart,
    evaluate_pre_payment_gates,
    ready_for_checkout,
)
from orderguard.enums import GateName, IntentStatus
from orderguard.models import CartLine, ObservedCart, PurchaseIntent


def _intent() -> PurchaseIntent:
    return PurchaseIntent(
        intent_id="intent-1",
        user_id="user-1",
        merchant="shop.example",
        items=[],
        maximum_total_paise=50000,
        missing_fields=[],
    )


def _intent_with_item() -> PurchaseIntent:
    return PurchaseIntent(
        intent_id="intent-1",
        user_id="user-1",
        merchant="shop.example",
        items=[{"requested_product": "banana", "quantity": 6, "unit": "piece"}],
        maximum_total_paise=50000,
    )


def _expectation() -> CartExpectation:
    return CartExpectation(
        merchant="shop.example",
        maximum_total_paise=50000,
        lines=[ApprovedCartLine(variant_id="banana", quantity=6, unit_price_paise=1200)],
    )


def _cart(quantity: int = 6) -> ObservedCart:
    return ObservedCart(
        merchant="shop.example",
        cart_id="cart-1",
        lines=[CartLine(sku="banana", variant_id="banana", quantity=quantity, unit_price_paise=1200)],
        total_paise=quantity * 1200,
    )


def _evidence(**changes) -> CheckoutEvidence:
    data = {
        "merchant_permitted": True,
        "cart_unique": True,
        "attributes_match": True,
        "items_available": True,
        "idempotency_free": True,
    }
    data.update(changes)
    return CheckoutEvidence(**data)


def test_confirmation_freezes_the_independently_read_cart_hash():
    intent = ready_for_checkout(_intent_with_item())
    result = confirm_cart(intent, _expectation(), _cart())
    assert result.confirmed
    assert result.intent.status is IntentStatus.CONFIRMED
    assert result.intent.confirmed_cart_hash == result.comparison.cart_hash


def test_changed_cart_invalidates_a_previous_confirmation():
    intent = confirm_cart(ready_for_checkout(_intent_with_item()), _expectation(), _cart()).intent
    gates = evaluate_pre_payment_gates(intent, _expectation(), _cart(quantity=60), _evidence())
    assert not gates.allow
    assert "G_QUANTITIES_MATCH" in gates.reasons
    assert "G_CONFIRMATION_MATCHES" in gates.reasons


def test_all_thirteen_pre_payment_gates_can_pass_with_typed_evidence():
    intent = confirm_cart(ready_for_checkout(_intent_with_item()), _expectation(), _cart()).intent
    gates = evaluate_pre_payment_gates(intent, _expectation(), _cart(), _evidence())
    assert gates.allow
    assert len(gates.passed) == 13
    assert not gates.failed


def test_one_missing_piece_of_evidence_blocks_the_whole_purchase():
    intent = confirm_cart(ready_for_checkout(_intent_with_item()), _expectation(), _cart()).intent
    gates = evaluate_pre_payment_gates(
        intent, _expectation(), _cart(), _evidence(idempotency_free=False)
    )
    assert not gates.allow
    assert "G_IDEMPOTENCY_FREE" in gates.reasons


def test_a_price_rise_under_the_cap_blocks_checkout():
    """End of the chain: the quoted-price check must actually stop a purchase.

    Everything an identity check can see is correct — right shop, right variant,
    six of them, INR, under the ₹500 cap. Only the price moved, from the ₹12
    quoted at search time to ₹80 in the cart. Checkout must not proceed.
    """
    overcharged = ObservedCart(
        merchant="shop.example",
        cart_id="cart-1",
        lines=[
            CartLine(sku="banana", variant_id="banana", quantity=6, unit_price_paise=8000)
        ],
        total_paise=48000,
    )

    confirmed = confirm_cart(
        ready_for_checkout(_intent_with_item()), _expectation(), overcharged
    )
    assert not confirmed.confirmed          # it never even gets a cart hash

    gates = evaluate_pre_payment_gates(
        _intent_with_item().model_copy(
            update={"status": IntentStatus.CONFIRMED, "confirmed_cart_hash": "x" * 64}
        ),
        _expectation(),
        overcharged,
        _evidence(),
    )
    assert not gates.allow
    assert GateName.PRICES_MATCH in gates.failed


# --- authorization freshness: TOCTOU ----------------------------------------

def test_a_confirmation_from_a_moment_ago_is_fresh():
    from datetime import datetime, timedelta, timezone

    confirmed = confirm_cart(ready_for_checkout(_intent_with_item()), _expectation(), _cart())
    assert confirmed.confirmed

    now = confirmed.intent.confirmed_at + timedelta(seconds=1)
    gates = evaluate_pre_payment_gates(
        confirmed.intent, _expectation(), _cart(), _evidence(), now=now,
    )
    assert GateName.AUTHORIZATION_FRESH in gates.passed


def test_a_confirmation_from_an_hour_ago_is_stale():
    """The exact TOCTOU gap: nothing about the cart changed, only how long ago
    it was looked at. A confirmation is proof of THIS moment, not a standing
    permission."""
    from datetime import timedelta

    confirmed = confirm_cart(ready_for_checkout(_intent_with_item()), _expectation(), _cart())
    assert confirmed.confirmed

    an_hour_later = confirmed.intent.confirmed_at + timedelta(hours=1)
    gates = evaluate_pre_payment_gates(
        confirmed.intent, _expectation(), _cart(), _evidence(), now=an_hour_later,
    )
    assert not gates.allow
    assert GateName.AUTHORIZATION_FRESH in gates.failed
    # nothing else about the cart is wrong — this is the ONLY failure
    assert gates.failed == [GateName.AUTHORIZATION_FRESH]


def test_the_freshness_window_is_configurable_but_never_optional():
    from datetime import timedelta

    confirmed = confirm_cart(ready_for_checkout(_intent_with_item()), _expectation(), _cart())
    thirty_min_later = confirmed.intent.confirmed_at + timedelta(minutes=30)

    # default 15-minute window: stale
    default = evaluate_pre_payment_gates(
        confirmed.intent, _expectation(), _cart(), _evidence(), now=thirty_min_later,
    )
    assert GateName.AUTHORIZATION_FRESH in default.failed

    # an explicitly wider window: fresh
    widened = evaluate_pre_payment_gates(
        confirmed.intent, _expectation(), _cart(), _evidence(),
        now=thirty_min_later, max_authorization_age=timedelta(hours=1),
    )
    assert GateName.AUTHORIZATION_FRESH in widened.passed


def test_an_intent_that_was_never_confirmed_has_no_authorization_to_be_fresh():
    gates = evaluate_pre_payment_gates(_intent_with_item(), _expectation(), _cart(), _evidence())
    assert GateName.AUTHORIZATION_FRESH in gates.failed
