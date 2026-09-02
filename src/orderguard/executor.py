"""The Secret Executor: the only module in this project, besides
``razorpay_client.py`` itself, that ever reads a Razorpay credential or
constructs a ``RazorpayClient``.

This is an extraction, not a new payment path for two of its three
functions (``find_order_by_receipt``, ``verify_and_capture`` — both wrap
exactly the same Razorpay calls ``app.py`` used to make directly). The
third, ``execute_create_order``, is the one function that actually starts a
chargeable Razorpay order, and that one now requires a valid Execution
Capability (``capability.py``) rather than accepting a freely-supplied
amount/currency/merchant from its caller — see that call's own docstring
for exactly what changed and why.

Why the import boundary exists at all: "the agent never imports payment
code" (see ``tests/test_architecture_boundaries.py``) proves an accidental
leak can't happen through the agent orchestrator. It does not, on its own,
prove there is no OTHER reachable path to Razorpay — a stray import in some
future module, a copy-pasted credential read, a second code path that also
happens to construct a client. Funnelling every credential read and every
``RazorpayClient`` construction through this one module turns "no other
path" from a claim about the current state of the code into an invariant
``test_architecture_boundaries.py`` can mechanically check on every change:
nothing outside this file (and ``razorpay_client.py``, which defines the
credential loader and the client itself) may reference
``RZP_KEY_ID``/``RZP_KEY_SECRET`` or construct a ``RazorpayClient``.

Honest limit, unchanged from before this file added capability
enforcement: this is import discipline plus a database-level atomic gate,
not a cryptographic capability nothing could forge, and not a separate
process/service — see ``capability.py``'s own docstring for exactly what
is and is not proven by this step.
"""

from __future__ import annotations

from sqlalchemy import Engine

from .capability import consume_capability
from .payment import Rejection, VerifiedPayment, verify_payment
from .razorpay_client import RazorpayClient, RazorpayError, client_from_env

__all__ = [
    "RazorpayError", "Rejection", "VerifiedPayment", "CapabilityRejected",
    "public_key_id", "execute_create_order", "find_order_by_receipt", "verify_and_capture",
]


class CapabilityRejected(RuntimeError):
    """Raised by ``execute_create_order`` before Razorpay is ever touched.
    ``reason`` is one of capability.py's CAPABILITY_* constants."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def public_key_id() -> str:
    """The PUBLIC half of the key pair only — safe to hand to a browser, per
    razorpay_client.py's own docstring and Razorpay's own documented pattern
    of embedding it directly in front-end code. Never the secret half."""
    key_id, _ = client_from_env()
    return key_id


async def execute_create_order(capability_db: Engine, capability_id: str) -> dict:
    """The only entry point that may start a chargeable Razorpay order —
    and, as of this version, it does not accept an amount, currency, or
    merchant from its caller at all. It atomically consumes the named
    capability first; if that fails for ANY reason (not found, expired,
    already consumed), ``CapabilityRejected`` is raised and Razorpay is
    never called — the RazorpayClient construction line below is
    unreachable on every rejection path. Only if consumption succeeds does
    it read amount_paise/currency/receipt/merchant from the CONSUMED
    CAPABILITY ROW itself and use those to build the real order. A caller
    cannot override them by passing different values alongside the
    capability_id, because there is no parameter for that to override.
    """
    capability, reason = consume_capability(capability_db, capability_id)
    if capability is None:
        raise CapabilityRejected(reason)

    key_id, key_secret = client_from_env()
    async with RazorpayClient(key_id, key_secret) as rzp:
        return await rzp.create_order(
            amount_paise=capability.amount_paise, currency=capability.currency,
            receipt=capability.receipt,
            notes={"merchant": capability.merchant, "capability_id": capability.capability_id},
        )


async def find_order_by_receipt(receipt: str) -> dict | None:
    key_id, key_secret = client_from_env()
    async with RazorpayClient(key_id, key_secret) as rzp:
        return await rzp.find_order_by_receipt(receipt)


async def verify_and_capture(
    *, order_id: str, payment_id: str, signature: str,
    expected_amount_paise: int, expected_currency: str,
) -> Rejection | VerifiedPayment:
    """The only function in this project that may confirm a payment claim.
    Wraps payment.py's own frozen 4-step contract (docs/API_CONTRACTS.md
    #6) unchanged — this module only relocates WHERE the credential is
    loaded and the client constructed, never what verify_payment itself
    checks."""
    key_id, key_secret = client_from_env()
    async with RazorpayClient(key_id, key_secret) as rzp:
        return await verify_payment(
            order_id=order_id, payment_id=payment_id, signature=signature,
            key_secret=key_secret, client=rzp,
            expected_amount_paise=expected_amount_paise, expected_currency=expected_currency,
        )
