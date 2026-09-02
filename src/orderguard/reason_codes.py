"""Short, stable codes for every gate — and the few real failures that aren't gates.

``GateName.QUANTITIES_MATCH`` is precise but verbose, and it is the only
handle callers currently have on "which check failed." This gives every one
of the 22 frozen gates (docs/GATES.md) a short ``OG-XXX-NNN`` code, plus
three for mechanisms that are real and already built but aren't gates:
signature verification (payment.py), webhook deduplication (webhooks.py),
and the PAYMENT_UNKNOWN ledger state (D-045).

Grouped by what kind of fact each failure is about, not by pre/post-payment —
a judge asking "show me every financial mismatch" wants FIN-* together,
whichever side of the payment they're on.
"""

from __future__ import annotations

from .enums import GateName

__all__ = ["REASON_CODES", "EXTRA_CODES", "code_for"]

REASON_CODES: dict[GateName, str] = {
    # --- identity: is this the right merchant, cart, item? -----------------
    GateName.MERCHANT_PERMITTED: "OG-ID-001",
    GateName.INTENT_VALID: "OG-ID-002",
    GateName.FIELDS_COMPLETE: "OG-ID-003",
    GateName.CART_UNIQUE: "OG-ID-004",
    GateName.ATTRIBUTES_MATCH: "OG-ID-005",
    GateName.ITEMS_AVAILABLE: "OG-ID-006",

    # --- quantity ------------------------------------------------------------
    GateName.QUANTITIES_MATCH: "OG-QTY-001",

    # --- financial: price, currency, amount, cap -----------------------------
    GateName.PRICES_MATCH: "OG-FIN-001",
    GateName.CURRENCY_MATCH: "OG-FIN-002",
    GateName.WITHIN_CAP: "OG-FIN-003",
    GateName.AMOUNT_MATCH: "OG-FIN-004",
    GateName.CURRENCY_MATCH_POST: "OG-FIN-005",

    # --- authorization: confirmation, freshness, single-use -------------------
    GateName.AUTHORIZATION_FRESH: "OG-AUTH-001",
    GateName.IDEMPOTENCY_FREE: "OG-AUTH-002",
    GateName.NO_PRIOR_EFFECT: "OG-AUTH-003",

    # --- state: has the cart or order moved under us? -------------------------
    GateName.CONFIRMATION_MATCHES: "OG-STATE-001",
    GateName.SINGLE_CANDIDATE: "OG-STATE-002",
    GateName.CORRELATION: "OG-STATE-003",
    GateName.ORDER_REPAIRABLE: "OG-STATE-004",
    GateName.NOT_EXPIRED: "OG-STATE-005",

    # --- payment: what Razorpay itself says --------------------------------
    GateName.PAYMENT_CAPTURED: "OG-PAY-001",
    GateName.NO_REFUND: "OG-PAY-002",
}

assert set(REASON_CODES) == set(GateName), (
    "every frozen gate must have a reason code — see docs/GATES.md"
)
assert len(set(REASON_CODES.values())) == len(REASON_CODES), "reason codes must be unique"


# Real, already-built failure modes that are not gates — payment.py's
# signature check, webhooks.py's dedup, and the PAYMENT_UNKNOWN ledger state
# (D-045) — but a judge reading a log line deserves the same short-code
# treatment for these as for the 22 gates.
EXTRA_CODES: dict[str, str] = {
    "INVALID_SIGNATURE": "OG-PAY-003",
    "DUPLICATE_WEBHOOK": "OG-PAY-004",
    "PAYMENT_UNKNOWN": "OG-PAY-005",
}


def code_for(name: GateName | str) -> str:
    """Look up a gate's short code, or an EXTRA_CODES key by name. Returns
    an empty string rather than raising for anything unrecognised — a
    missing code should degrade a display, never crash a response."""
    if isinstance(name, GateName):
        return REASON_CODES.get(name, "")
    try:
        return REASON_CODES.get(GateName(name), "") or EXTRA_CODES.get(name, "")
    except ValueError:
        return EXTRA_CODES.get(name, "")
