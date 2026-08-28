"""Confirmation and deterministic pre-payment gates.

Nothing here calls a model, a merchant, or Razorpay. The caller supplies typed
evidence gathered by trusted adapters and the idempotency store. Missing
evidence is impossible to silently treat as a pass because every evidence field
is required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict

from .cart_verifier import CartComparison, CartExpectation, compare_cart
from .enums import GateName, IntentStatus
from .models import GateResult, ObservedCart, PurchaseIntent

__all__ = [
    "CheckoutEvidence",
    "ConfirmationResult",
    "confirm_cart",
    "evaluate_pre_payment_gates",
    "ready_for_checkout",
    "DEFAULT_AUTHORIZATION_TTL",
]

# How long a confirmation stays valid before payment must be re-confirmed.
# 15 minutes matches the order of magnitude Razorpay's own Checkout uses for a
# created order before it goes stale, so the two expiries fail at a similar
# horizon rather than one silently outliving the other.
DEFAULT_AUTHORIZATION_TTL = timedelta(minutes=15)

STRICT = ConfigDict(extra="forbid", frozen=True)


class CheckoutEvidence(BaseModel):
    """Non-LLM facts needed for the gates the cart itself cannot prove."""

    model_config = STRICT

    merchant_permitted: bool
    cart_unique: bool
    attributes_match: bool
    items_available: bool
    idempotency_free: bool


class ConfirmationResult(BaseModel):
    model_config = STRICT

    intent: PurchaseIntent | None = None
    comparison: CartComparison

    @property
    def confirmed(self) -> bool:
        return self.intent is not None


def ready_for_checkout(intent: PurchaseIntent) -> PurchaseIntent:
    """Advance only a complete request. Product choice is recorded separately."""
    if not intent.is_complete:
        raise ValueError("cannot prepare an incomplete intent for checkout")
    return intent.model_copy(update={"status": IntentStatus.READY_FOR_CHECKOUT})


def confirm_cart(
    intent: PurchaseIntent, expectation: CartExpectation, observed: ObservedCart
) -> ConfirmationResult:
    """Freeze a cart hash only after the independently read cart matches."""
    if intent.status is not IntentStatus.READY_FOR_CHECKOUT:
        raise ValueError("only a checkout-ready intent may be confirmed")

    comparison = compare_cart(expectation, observed)
    if not comparison.matches:
        return ConfirmationResult(comparison=comparison)

    return ConfirmationResult(
        intent=intent.model_copy(
            update={
                "status": IntentStatus.CONFIRMED,
                "confirmed_cart_hash": comparison.cart_hash,
                "confirmed_at": datetime.now(timezone.utc),
            }
        ),
        comparison=comparison,
    )


def evaluate_pre_payment_gates(
    intent: PurchaseIntent,
    expectation: CartExpectation,
    observed: ObservedCart,
    evidence: CheckoutEvidence,
    *,
    now: datetime | None = None,
    max_authorization_age: timedelta = DEFAULT_AUTHORIZATION_TTL,
) -> GateResult:
    """Run every pre-payment gate and block if a single fact is false.

    ``now`` defaults to the real clock; a caller passes a fixed value only to
    test the boundary deterministically (see tests/test_checkout_guard.py).
    Never let it default to anything a caller could quietly leave unset in a
    way that skips the check — there is no "skip" state, only a real timestamp.
    """
    now = now or datetime.now(timezone.utc)
    comparison = compare_cart(expectation, observed)
    confirmation_matches = (
        intent.status is IntentStatus.CONFIRMED
        and intent.confirmed_cart_hash == comparison.cart_hash
    )
    # TOCTOU: a confirmation is proof the user approved THIS cart at THIS
    # moment, not a standing permission. Without an expiry, a hash confirmed
    # once could authorise a checkout an hour, a day, or a week later, on
    # prices and stock that may no longer be true. See D-035.
    authorization_age = (
        now - intent.confirmed_at if intent.confirmed_at is not None else None
    )
    authorization_fresh = (
        authorization_age is not None and authorization_age <= max_authorization_age
    )
    checks: dict[GateName, tuple[bool, str]] = {
        GateName.MERCHANT_PERMITTED: (
            # Two separate facts, both required. `evidence.merchant_permitted`
            # says the APPROVED merchant is on the allowed list.
            # `comparison.matches_merchant` says the cart IN FRONT OF US is
            # actually that merchant. Before this, only the second was caught
            # — and only as a side effect of the cart-hash including the
            # merchant name, alongside every other structural check, which
            # has its own dedicated gate as a first line of defense. Merchant
            # was the one category relying solely on the hash catch-all
            # (found while building diagnostics.py; every other attack kind
            # already had both a specific gate AND the hash backup).
            evidence.merchant_permitted and comparison.matches_merchant,
            f"Merchant {observed.merchant} is not the one you approved."
            if not comparison.matches_merchant
            else f"Merchant {observed.merchant} is not in your approved list.",
        ),
        GateName.INTENT_VALID: (
            intent.status is IntentStatus.CONFIRMED,
            "The request is not a confirmed purchase.",
        ),
        GateName.FIELDS_COMPLETE: (
            intent.is_complete,
            "The request still has missing fields.",
        ),
        GateName.CART_UNIQUE: (
            evidence.cart_unique and bool(observed.cart_id),
            "The cart cannot be uniquely identified.",
        ),
        GateName.ATTRIBUTES_MATCH: (
            evidence.attributes_match,
            "The cart does not prove the required product attributes.",
        ),
        GateName.QUANTITIES_MATCH: (
            comparison.matches_quantities,
            "Cart variants or quantities differ from the approved cart.",
        ),
        GateName.PRICES_MATCH: (
            comparison.matches_prices,
            "The cart charges a different price from the one you were quoted.",
        ),
        GateName.ITEMS_AVAILABLE: (
            evidence.items_available,
            "At least one item is no longer confirmed available.",
        ),
        GateName.CURRENCY_MATCH: (
            comparison.matches_currency,
            "Cart currency differs from the approved currency.",
        ),
        GateName.WITHIN_CAP: (
            comparison.within_cap,
            "Cart total exceeds the approved spending cap.",
        ),
        GateName.CONFIRMATION_MATCHES: (
            confirmation_matches,
            "The cart changed after you confirmed it, or has not been confirmed.",
        ),
        GateName.AUTHORIZATION_FRESH: (
            authorization_fresh,
            "Too long has passed since you confirmed this cart. Please review "
            "and confirm it again before paying."
            if authorization_age is not None
            else "This purchase was never confirmed.",
        ),
        GateName.IDEMPOTENCY_FREE: (
            evidence.idempotency_free,
            "This purchase has already had a successful effect.",
        ),
    }
    return GateResult.from_checks(checks)
