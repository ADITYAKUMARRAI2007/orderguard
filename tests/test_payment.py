"""verify_payment: the only function allowed to say a purchase is paid.

Every test here is either "a correct claim is accepted" or "a specific way of
lying about a payment is rejected". The browser's own success message is
deliberately never part of the input — only order_id, payment_id, signature,
and what Razorpay independently reports.
"""

import hashlib
import hmac

import pytest

from orderguard.payment import Rejection, VerifiedPayment, verify_payment
from orderguard.razorpay_client import RazorpayError

SECRET = "test_secret_key"
ORDER_ID = "order_ABC123"
PAYMENT_ID = "pay_XYZ789"


def _sign(order_id: str, payment_id: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


class _FakeClient:
    """Stands in for RazorpayClient. Records whether fetch_payment was called."""

    def __init__(self, record: dict | Exception):
        self._record = record
        self.fetch_calls = 0

    async def fetch_payment(self, payment_id: str) -> dict:
        self.fetch_calls += 1
        if isinstance(self._record, Exception):
            raise self._record
        return self._record


def _captured(**overrides) -> dict:
    record = {
        "id": PAYMENT_ID, "order_id": ORDER_ID, "status": "captured",
        "amount": 29900, "currency": "INR", "method": "upi",
    }
    record.update(overrides)
    return record


# --- the correct case --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_genuinely_captured_payment_is_verified():
    client = _FakeClient(_captured())
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, VerifiedPayment)
    assert result.payment_id == PAYMENT_ID
    assert result.order_id == ORDER_ID
    assert result.amount_paise == 29900
    assert result.status == "captured"
    assert client.fetch_calls == 1


# --- the signature: checked first, checked exactly ---------------------------

@pytest.mark.asyncio
async def test_a_wrong_signature_is_rejected_before_any_network_call():
    """The order matters: a forged signature must cost nothing to reject."""
    client = _FakeClient(_captured())
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature="0" * 64,
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert "signature" in result.reason
    assert client.fetch_calls == 0          # never reached the network


@pytest.mark.asyncio
async def test_a_signature_for_a_different_payment_id_is_rejected():
    """Someone else's valid signature, replayed against this payment_id."""
    client = _FakeClient(_captured())
    wrong_sig = _sign(ORDER_ID, "pay_SOMEONE_ELSE")
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=wrong_sig,
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert client.fetch_calls == 0


@pytest.mark.asyncio
async def test_a_signature_signed_with_the_wrong_secret_is_rejected():
    client = _FakeClient(_captured())
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID,
        signature=_sign(ORDER_ID, PAYMENT_ID, secret="attacker_guessed_this"),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert client.fetch_calls == 0


@pytest.mark.parametrize("missing", ["order_id", "payment_id", "signature"])
@pytest.mark.asyncio
async def test_a_missing_field_is_rejected_without_touching_the_network(missing):
    client = _FakeClient(_captured())
    kwargs = {
        "order_id": ORDER_ID, "payment_id": PAYMENT_ID,
        "signature": _sign(ORDER_ID, PAYMENT_ID), "key_secret": SECRET,
        "client": client, "expected_amount_paise": 29900,
    }
    kwargs[missing] = ""
    result = await verify_payment(**kwargs)
    assert isinstance(result, Rejection)
    assert client.fetch_calls == 0


# --- the independent fetch: nothing from the caller is trusted ---------------

@pytest.mark.asyncio
async def test_a_payment_belonging_to_a_different_order_is_rejected():
    """The signature was valid, but Razorpay's own record disagrees.

    This is what stops one valid (order, payment) signature pair being replayed
    against a different order_id that the attacker also knows.
    """
    client = _FakeClient(_captured(order_id="order_SOMETHING_ELSE"))
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert "order_SOMETHING_ELSE" in result.reason


@pytest.mark.parametrize("status", ["created", "authorized", "failed", "refunded"])
@pytest.mark.asyncio
async def test_anything_other_than_captured_is_rejected(status):
    """AUTHORIZED means the bank approved but no money moved yet, and it
    auto-refunds if left alone. Only CAPTURED means the money is actually taken."""
    client = _FakeClient(_captured(status=status))
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert status in result.reason


@pytest.mark.asyncio
async def test_the_wrong_amount_is_rejected_even_when_captured():
    """A captured payment for a DIFFERENT amount than the confirmed cart."""
    client = _FakeClient(_captured(amount=100))       # paid 1 rupee, not 299
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert "100" in result.reason and "29900" in result.reason


@pytest.mark.asyncio
async def test_a_float_amount_is_rejected_never_silently_coerced():
    client = _FakeClient(_captured(amount=299.0))
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)


@pytest.mark.asyncio
async def test_the_wrong_currency_is_rejected():
    client = _FakeClient(_captured(currency="USD"))
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
        expected_currency="INR",
    )
    assert isinstance(result, Rejection)


@pytest.mark.asyncio
async def test_the_refunded_amount_is_carried_through_for_the_no_refund_gate():
    """amount_refunded is additive to the frozen 4-step contract (docs/
    API_CONTRACTS.md #6) -- captured from the SAME independent fetch, for
    checkout_guard.py's G_NO_REFUND gate to check without a second call."""
    client = _FakeClient(_captured(amount_refunded=5000))
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, VerifiedPayment)
    assert result.amount_refunded_paise == 5000


@pytest.mark.asyncio
async def test_a_captured_payment_with_no_refund_field_defaults_to_zero():
    client = _FakeClient(_captured())
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, VerifiedPayment)
    assert result.amount_refunded_paise == 0


@pytest.mark.asyncio
async def test_a_network_failure_during_the_independent_fetch_is_a_rejection():
    """Never crash, and never treat 'could not check' as 'must be fine'."""
    client = _FakeClient(RazorpayError("timed out"))
    result = await verify_payment(
        order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=_sign(ORDER_ID, PAYMENT_ID),
        key_secret=SECRET, client=client, expected_amount_paise=29900,
    )
    assert isinstance(result, Rejection)
    assert "timed out" in result.reason


# --- the client itself refuses live keys -------------------------------------

def test_the_razorpay_client_refuses_a_non_test_key():
    from orderguard.razorpay_client import RazorpayClient

    with pytest.raises(RazorpayError, match="test-mode"):
        RazorpayClient("rzp_live_shouldnotbeused", "secret")


def test_the_razorpay_client_accepts_a_test_key():
    from orderguard.razorpay_client import RazorpayClient

    client = RazorpayClient("rzp_test_abc123", "secret")
    assert client.key_id == "rzp_test_abc123"
