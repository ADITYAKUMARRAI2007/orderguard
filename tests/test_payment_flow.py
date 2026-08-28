"""The whole payment path, wired into the running app.

This is the test that matters most in the file: 70 identical verify calls,
after a genuine Razorpay payment, produce exactly one entry in order history
and exactly one captured ledger row — the buildathon's own framing of
"at-least-once events, exactly-once business effect", proven at the level a
real client would actually call it.

No network is used. ``RazorpayClient`` is replaced with a fake that behaves
like the real API for exactly the calls this code makes; ``verify_payment``
itself is the real function, computing and checking a real HMAC signature.
"""

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from orderguard import app as app_module
from orderguard.commerce import Offer, ScoredOffer, SearchOutcome
from orderguard.ledger import LedgerStatus, get_entry, ledger_engine
from orderguard.llm import StubProvider
from orderguard.memory import memory_engine, recent_orders
from orderguard.models import CartLine, ObservedCart

KEY_ID = "rzp_test_fake"
KEY_SECRET = "fake_secret_for_tests"
FAKE_ORDER_ID = "order_fake_00001"


def _sign(order_id: str, payment_id: str) -> str:
    return hmac.new(
        KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


class _FakeRazorpayClient:
    """Behaves like the real API for exactly the two calls this code makes."""

    create_calls = 0
    fetch_calls = 0
    captured_amount = 6600 * 2   # 2 x milk at 6600 paise, matching _outcome()

    def __init__(self, key_id, key_secret):
        assert key_id == KEY_ID and key_secret == KEY_SECRET

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def create_order(self, *, amount_paise, currency, receipt, notes):
        type(self).create_calls += 1
        return {"id": FAKE_ORDER_ID}

    async def fetch_payment(self, payment_id):
        type(self).fetch_calls += 1
        return {
            "id": payment_id, "order_id": FAKE_ORDER_ID, "status": "captured",
            "amount": self.captured_amount, "currency": "INR", "method": "upi",
        }


def _outcome() -> SearchOutcome:
    offer = Offer(
        store="slurrpfarm.com", store_label="Slurrp Farm", product_id="p1",
        variant_id="v1", title="Milk", price_minor=6600, currency="INR", available=True,
    )
    return SearchOutcome(
        query="milk", quantity=2, budget_minor=50000,
        offers=[ScoredOffer(
            offer=offer, relevance=1.0, in_stock=True, priced=True,
            within_budget=True, line_total_minor=13200,
        )],
        stores_searched=["Slurrp Farm"],
    )


class _Adapter:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def add_to_cart(self, variant_id, quantity, cart_id=None):
        return ObservedCart(
            merchant="slurrpfarm.com", cart_id="cart-1",
            lines=[CartLine(sku=variant_id, variant_id=variant_id, quantity=quantity, unit_price_paise=6600)],
            total_paise=quantity * 6600,
        )

    async def read_cart(self, cart_id):
        return await self.add_to_cart("v1", 2, cart_id)


@pytest.fixture
def client(monkeypatch):
    app_module._SESSIONS.clear()
    monkeypatch.setattr(app_module, "MEMORY", memory_engine(":memory:"))
    monkeypatch.setattr(app_module, "LEDGER", ledger_engine(":memory:"))
    monkeypatch.setattr(app_module, "RazorpayClient", _FakeRazorpayClient)
    monkeypatch.setenv("RZP_KEY_ID", KEY_ID)
    monkeypatch.setenv("RZP_KEY_SECRET", KEY_SECRET)
    _FakeRazorpayClient.create_calls = 0
    _FakeRazorpayClient.fetch_calls = 0

    stub = StubProvider(extra_answers={
        "slurrpfarm.com: two litres of milk, budget 500 rupees": {
            "merchant": "slurrpfarm.com",
            "items": [{"requested_product": "milk", "quantity": 2, "unit": "litre"}],
            "maximum_total_paise": 50000,
        },
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    async def search(*args, **kwargs):
        return _outcome()

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _Adapter)
    return TestClient(app_module.app)


def _confirmed_session(client) -> str:
    session_id = client.post("/api/sessions", json={
        "user_id": "buyer1",
        "request_text": "slurrpfarm.com: two litres of milk, budget 500 rupees",
    }).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/items/0/search")
    client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
    })
    confirmed = client.post(f"/api/sessions/{session_id}/confirm").json()
    assert confirmed["intent"] is not None, confirmed
    return session_id


# --- the headless order leg --------------------------------------------------

def test_creating_a_payment_order_runs_the_gates_and_calls_razorpay_once(client):
    session_id = _confirmed_session(client)

    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    assert order["status"] == "pending"
    assert order["razorpay_order_id"] == FAKE_ORDER_ID
    assert order["amount_paise"] == 13200
    assert order["gates_passed"] == order["gates_total"] == 13
    assert _FakeRazorpayClient.create_calls == 1


def test_creating_the_order_twice_never_creates_a_second_razorpay_order(client):
    session_id = _confirmed_session(client)

    first = client.post(f"/api/sessions/{session_id}/payment/order").json()
    second = client.post(f"/api/sessions/{session_id}/payment/order").json()

    assert first["razorpay_order_id"] == second["razorpay_order_id"]
    assert _FakeRazorpayClient.create_calls == 1


def test_payment_is_refused_before_the_cart_is_confirmed(client):
    session_id = client.post("/api/sessions", json={
        "user_id": "buyer1",
        "request_text": "slurrpfarm.com: two litres of milk, budget 500 rupees",
    }).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/items/0/search")
    client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
    })
    # confirmed, but never actually confirmed
    refused = client.post(f"/api/sessions/{session_id}/payment/order")
    assert refused.status_code == 409
    assert _FakeRazorpayClient.create_calls == 0


# --- the property that matters: 70 duplicate calls, one business effect -----

def test_seventy_verify_calls_after_one_real_payment_capture_exactly_once(client):
    """The scenario, run through the actual endpoints a client would call."""
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    payment_id = "pay_real_00001"
    signature = _sign(order["razorpay_order_id"], payment_id)

    responses = [
        client.post(f"/api/sessions/{session_id}/payment/verify", json={
            "razorpay_payment_id": payment_id, "razorpay_signature": signature,
        }).json()
        for _ in range(70)
    ]

    assert all(r["captured"] for r in responses)
    assert all(r["payment_id"] == payment_id for r in responses)
    # exactly one of the seventy did the actual work
    assert sum(1 for r in responses if not r["already_captured"]) == 1
    assert sum(1 for r in responses if r["already_captured"]) == 69

    # order history was written ONCE, not seventy times
    from orderguard.app import MEMORY
    assert len(recent_orders(MEMORY, "buyer1")) == 1

    from orderguard.app import LEDGER
    session = app_module._SESSIONS[session_id]
    intent = session.intent
    key = f"{intent.merchant}|{intent.intent_id}|purchase|{intent.confirmed_cart_hash}"
    entry = get_entry(LEDGER, key)
    assert entry.status is LedgerStatus.CAPTURED
    assert entry.razorpay_payment_id == payment_id


def test_a_wrong_signature_is_rejected_and_a_later_correct_one_still_works(client):
    """Rejecting a bad attempt must not burn the user's real chance to pay."""
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    bad = client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": "pay_real_00001", "razorpay_signature": "0" * 64,
    }).json()
    assert bad["captured"] is False
    assert "signature" in bad["reason"]

    good_signature = _sign(order["razorpay_order_id"], "pay_real_00001")
    good = client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": "pay_real_00001", "razorpay_signature": good_signature,
    }).json()
    assert good["captured"] is True

    from orderguard.app import MEMORY
    assert len(recent_orders(MEMORY, "buyer1")) == 1


def test_verify_without_an_order_first_is_refused(client):
    session_id = _confirmed_session(client)
    refused = client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": "pay_x", "razorpay_signature": "y" * 64,
    })
    assert refused.status_code == 409


def test_a_payment_for_the_wrong_amount_is_rejected(client, monkeypatch):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    monkeypatch.setattr(_FakeRazorpayClient, "captured_amount", 100)   # paid re1, not the real total
    signature = _sign(order["razorpay_order_id"], "pay_underpaid")

    result = client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": "pay_underpaid", "razorpay_signature": signature,
    }).json()

    assert result["captured"] is False
    assert "13200" in result["reason"]

    from orderguard.app import MEMORY
    assert recent_orders(MEMORY, "buyer1") == []
