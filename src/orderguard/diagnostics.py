"""Turn a gate failure into something a screen can show, not just a sentence.

``GateResult.reasons`` gives one English sentence per failed gate — enough for
an API response, not enough for a demo. A judge asking "what exactly did the
agent get wrong?" deserves an answer shaped like a diff: what was approved,
what showed up instead, and which named gate caught the difference.

This module adds nothing to what a gate decides. It reads the same
``CartComparison`` and ``PurchaseIntent`` the gates already computed and
renders the parts that have a natural expected/actual pairing as structured
values instead of prose. Nothing here can change ``allow`` — it runs strictly
after the real decision and only describes it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from .cart_verifier import CartExpectation, compare_cart
from .checkout_guard import DEFAULT_AUTHORIZATION_TTL
from .enums import GateName
from .models import GateResult, ObservedCart, PurchaseIntent
from .reason_codes import code_for

__all__ = ["Diagnostic", "diagnose"]

STRICT = ConfigDict(extra="forbid", frozen=True)


class Diagnostic(BaseModel):
    """One failed gate, with what was expected and what was actually seen.

    ``expected``/``actual`` are plain JSON-safe values (str, int, dict) rather
    than model instances, so this serialises directly for a UI or a log line
    with no second translation step.
    """

    model_config = STRICT

    decision: str = "BLOCK"
    reason_code: str
    code: str = ""      # OG-XXX-NNN short form (reason_codes.py) — set by diagnose()
    message: str
    expected: dict | str | int | None = None
    actual: dict | str | int | None = None


def _quantities_diagnostic(expectation: CartExpectation, observed: ObservedCart) -> Diagnostic | None:
    expected_qty = {line.variant_id: line.quantity for line in expectation.lines}
    observed_qty: dict[str, int] = {}
    for line in observed.lines:
        key = line.variant_id or line.sku
        observed_qty[key] = observed_qty.get(key, 0) + line.quantity
    if expected_qty == observed_qty:
        return None
    return Diagnostic(
        reason_code=str(GateName.QUANTITIES_MATCH),
        message="Cart variants or quantities differ from what was approved.",
        expected=expected_qty,
        actual=observed_qty,
    )


def _prices_diagnostic(expectation: CartExpectation, observed: ObservedCart) -> Diagnostic | None:
    expected_totals = {line.variant_id: line.unit_price_paise * line.quantity for line in expectation.lines}
    observed_totals: dict[str, int] = {}
    for line in observed.lines:
        key = line.variant_id or line.sku
        observed_totals[key] = observed_totals.get(key, 0) + (line.line_total_paise or 0)

    mismatched = {
        variant: {"quoted_paise": expected_totals[variant], "charged_paise": observed_totals[variant]}
        for variant in expected_totals
        if variant in observed_totals and observed_totals[variant] != expected_totals[variant]
    }
    if not mismatched:
        return None
    return Diagnostic(
        reason_code=str(GateName.PRICES_MATCH),
        message="The cart charges a different price from the one quoted.",
        expected={v: d["quoted_paise"] for v, d in mismatched.items()},
        actual={v: d["charged_paise"] for v, d in mismatched.items()},
    )


def _merchant_diagnostic(expectation: CartExpectation, observed: ObservedCart) -> Diagnostic | None:
    if observed.merchant.lower() == expectation.merchant.lower():
        return None
    return Diagnostic(
        reason_code=str(GateName.MERCHANT_PERMITTED),
        message="The cart belongs to a different store than the one approved.",
        expected=expectation.merchant,
        actual=observed.merchant,
    )


def _currency_diagnostic(expectation: CartExpectation, observed: ObservedCart) -> Diagnostic | None:
    if observed.currency.upper() == expectation.currency.upper():
        return None
    return Diagnostic(
        reason_code=str(GateName.CURRENCY_MATCH),
        message="The cart currency does not match what was approved.",
        expected=expectation.currency.upper(),
        actual=observed.currency.upper(),
    )


def _cap_diagnostic(expectation: CartExpectation, observed: ObservedCart) -> Diagnostic | None:
    total = observed.total_paise
    if total is not None and total <= expectation.maximum_total_paise:
        return None
    return Diagnostic(
        reason_code=str(GateName.WITHIN_CAP),
        message="The cart total exceeds the approved spending limit.",
        expected={"maximum_total_paise": expectation.maximum_total_paise},
        actual={"cart_total_paise": total},
    )


def _confirmation_diagnostic(intent: PurchaseIntent, cart_hash: str) -> Diagnostic | None:
    if intent.confirmed_cart_hash == cart_hash:
        return None
    return Diagnostic(
        reason_code=str(GateName.CONFIRMATION_MATCHES),
        message="The cart changed after it was confirmed, or was never confirmed.",
        expected={"confirmed_cart_hash": intent.confirmed_cart_hash},
        actual={"current_cart_hash": cart_hash},
    )


def _freshness_diagnostic(
    intent: PurchaseIntent, now: datetime, ttl: timedelta
) -> Diagnostic | None:
    if intent.confirmed_at is None:
        return Diagnostic(
            reason_code=str(GateName.AUTHORIZATION_FRESH),
            message="This purchase was never confirmed.",
            expected={"confirmed": True}, actual={"confirmed": False},
        )
    age = now - intent.confirmed_at
    if age <= ttl:
        return None
    return Diagnostic(
        reason_code=str(GateName.AUTHORIZATION_FRESH),
        message="Too long has passed since this cart was confirmed.",
        expected={"max_age_seconds": int(ttl.total_seconds())},
        actual={"age_seconds": int(age.total_seconds())},
    )


def diagnose(
    intent: PurchaseIntent,
    expectation: CartExpectation,
    observed: ObservedCart,
    gates: GateResult,
    *,
    now: datetime | None = None,
    max_authorization_age: timedelta = DEFAULT_AUTHORIZATION_TTL,
) -> list[Diagnostic]:
    """One structured entry per failed gate that has a natural expected/actual
    pairing. Gates whose failure is a fact about external evidence rather than
    a comparison (MERCHANT_PERMITTED's allowlist check, ITEMS_AVAILABLE,
    IDEMPOTENCY_FREE) are not diagnosed here — there is nothing to diff, only
    a fact to state, and ``gates.reasons`` already states it.

    Runs strictly downstream of ``gates``: it describes ``gates.failed``, it
    never recomputes whether something failed.
    """
    from datetime import timezone as _tz

    now = now or datetime.now(_tz.utc)
    comparison = compare_cart(expectation, observed)
    candidates = {
        GateName.QUANTITIES_MATCH: lambda: _quantities_diagnostic(expectation, observed),
        GateName.PRICES_MATCH: lambda: _prices_diagnostic(expectation, observed),
        GateName.MERCHANT_PERMITTED: lambda: _merchant_diagnostic(expectation, observed),
        GateName.CURRENCY_MATCH: lambda: _currency_diagnostic(expectation, observed),
        GateName.WITHIN_CAP: lambda: _cap_diagnostic(expectation, observed),
        GateName.CONFIRMATION_MATCHES: lambda: _confirmation_diagnostic(intent, comparison.cart_hash),
        GateName.AUTHORIZATION_FRESH: lambda: _freshness_diagnostic(intent, now, max_authorization_age),
    }

    out: list[Diagnostic] = []
    for name in gates.failed:
        build = candidates.get(name)
        if build is None:
            continue
        diagnostic = build()
        if diagnostic is not None:
            out.append(diagnostic.model_copy(update={"code": code_for(name)}))
    return out
