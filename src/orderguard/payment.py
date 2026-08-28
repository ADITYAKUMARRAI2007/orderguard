"""``verify_payment`` — the only function allowed to say a purchase is paid.

Frozen contract, docs/API_CONTRACTS.md #6. Four steps, in this order, and the
order is load-bearing:

1. HMAC-SHA256 over ``f"{order_id}|{payment_id}"`` with the key secret
2. **Constant-time** comparison against the given signature
3. Only then, an **independent fetch** of the payment from Razorpay
4. Equality on ``status == "captured"``, ``amount``, ``currency``, ``order_id``

Signature checked *before* the network call, not after: an attacker sending a
thousand guessed signatures should not each cost us a Razorpay API call, and a
bad signature should never reach step 3 at all.

**The browser's success message is evidence of nothing.** ``checkout.js``'s
completion handler proves the browser rendered a success screen. It says
nothing about whether the bank actually moved money, whether the amount
matches, or whether the order is the one we created. Every one of those is
re-checked here, independently, against Razorpay's own record.

No other function in this codebase may mark a purchase complete. If a caller
wants to know whether money moved, it calls this and nothing else.
"""

from __future__ import annotations

import hmac
import hashlib

from pydantic import BaseModel, ConfigDict, Field

from .razorpay_client import RazorpayClient, RazorpayError

__all__ = ["VerifiedPayment", "Rejection", "verify_payment"]

STRICT = ConfigDict(extra="forbid", frozen=True)


class VerifiedPayment(BaseModel):
    """Every field here came from Razorpay's own record, not the browser."""

    model_config = STRICT

    payment_id: str
    order_id: str
    amount_paise: int = Field(ge=0)
    currency: str
    status: str
    method: str = ""


class Rejection(BaseModel):
    """Why a payment was NOT accepted. Never a bare bool — always a reason."""

    model_config = STRICT

    reason: str


def _expected_signature(order_id: str, payment_id: str, key_secret: str) -> str:
    return hmac.new(
        key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


async def verify_payment(
    *,
    order_id: str,
    payment_id: str,
    signature: str,
    key_secret: str,
    client: RazorpayClient,
    expected_amount_paise: int,
    expected_currency: str = "INR",
) -> VerifiedPayment | Rejection:
    """Check a payment claim against Razorpay's own record. Never raises.

    ``expected_amount_paise`` and ``expected_currency`` come from the confirmed
    cart, not from this call's caller trusting the payment record blindly — a
    captured payment for the WRONG amount must still be rejected.
    """
    if not order_id or not payment_id or not signature:
        return Rejection(reason="order id, payment id and signature are all required")

    # Step 1 + 2: the signature, checked before any network call is made.
    expected = _expected_signature(order_id, payment_id, key_secret)
    if not hmac.compare_digest(expected, signature):
        return Rejection(reason="signature does not match — this claim is not trusted")

    # Step 3: independent fetch. What the browser reported is now irrelevant;
    # only what Razorpay itself says about this payment_id counts from here.
    try:
        record = await client.fetch_payment(payment_id)
    except RazorpayError as exc:
        return Rejection(reason=f"could not independently verify: {exc}")

    # Step 4: equality, with zero tolerance. No field is trusted from anywhere
    # but this fetch.
    fetched_order_id = str(record.get("order_id") or "")
    status = str(record.get("status") or "")
    amount = record.get("amount")
    currency = str(record.get("currency") or "")

    if fetched_order_id != order_id:
        return Rejection(
            reason=f"payment {payment_id} belongs to order {fetched_order_id!r}, "
                   f"not {order_id!r} — refusing to attach it here"
        )
    if status != "captured":
        return Rejection(reason=f"payment status is {status!r}, not captured")
    if not isinstance(amount, int) or isinstance(amount, bool):
        return Rejection(reason=f"payment amount is not a plain integer: {amount!r}")
    if amount != expected_amount_paise:
        return Rejection(
            reason=f"paid {amount} paise; the confirmed cart was "
                   f"{expected_amount_paise} paise"
        )
    if currency.upper() != expected_currency.upper():
        return Rejection(
            reason=f"paid in {currency}; the confirmed cart was {expected_currency}"
        )

    return VerifiedPayment(
        payment_id=payment_id, order_id=order_id, amount_paise=amount,
        currency=currency.upper(), status=status, method=str(record.get("method") or ""),
    )
