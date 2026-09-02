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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from orderguard import app as app_module
from orderguard import executor
from orderguard.audit import audit_engine
from orderguard.authorization import authorization_db_engine, verify_authorization
from orderguard.commerce import Offer, ScoredOffer, SearchOutcome
from orderguard.ledger import LedgerStatus, get_entry, ledger_engine
from orderguard.llm import StubProvider
from orderguard.memory import memory_engine, recent_orders
from orderguard.models import CartLine, ObservedCart
from orderguard.webhooks import webhook_log_engine

KEY_ID = "rzp_test_fake"
KEY_SECRET = "fake_secret_for_tests"
WEBHOOK_SECRET = "whsec_fake_for_tests"
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
    refunded_amount = 0

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
            "amount_refunded": self.refunded_amount,
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
    monkeypatch.setattr(app_module, "AUDIT", audit_engine(":memory:"))
    monkeypatch.setattr(app_module, "AUTH_DB", authorization_db_engine(":memory:"))
    monkeypatch.setattr(app_module, "SIGNING_KEY", Ed25519PrivateKey.generate())
    monkeypatch.setattr(app_module, "WEBHOOK_LOG", webhook_log_engine(":memory:"))
    monkeypatch.setattr(executor, "RazorpayClient", _FakeRazorpayClient)
    monkeypatch.setenv("RZP_KEY_ID", KEY_ID)
    monkeypatch.setenv("RZP_KEY_SECRET", KEY_SECRET)
    monkeypatch.setenv("RZP_WEBHOOK_SECRET", WEBHOOK_SECRET)
    _FakeRazorpayClient.create_calls = 0
    _FakeRazorpayClient.fetch_calls = 0
    _FakeRazorpayClient.refunded_amount = 0

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


def test_the_payment_order_carries_a_signed_authorization(client):
    """The receipt this project can hand a judge: independently verifiable,
    tied to the real amount and merchant, not a mutable flag anywhere."""
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    auth = order["authorization"]
    assert auth is not None
    assert auth["merchant"] == "slurrpfarm.com"
    assert auth["amount_paise"] == 13200
    assert auth["currency"] == "INR"
    assert auth["audit_tip"]          # linked to a real AuditEvent, not None

    from orderguard.authorization import Authorization
    reconstructed = Authorization.model_validate(auth)
    assert verify_authorization(reconstructed, public_key=app_module.SIGNING_KEY.public_key())


def test_a_second_order_call_for_the_same_session_does_not_reissue_authorization(client):
    session_id = _confirmed_session(client)
    first = client.post(f"/api/sessions/{session_id}/payment/order").json()
    second = client.post(f"/api/sessions/{session_id}/payment/order").json()

    assert first["authorization"]["authorization_id"] == second["authorization"]["authorization_id"]
    assert first["authorization"]["signature"] == second["authorization"]["signature"]


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


# --- F-031: a merchant-side change after confirmation is now caught --------

class _MutatingAdapter:
    """Behaves exactly like ``_Adapter`` for the first ``read_cart`` (the one
    ``select_offer`` and ``confirm`` see), then quietly starts returning a
    different cart from the SAME id — modelling the merchant's own state
    changing between confirmation and payment, not a client tampering with
    anything. Before F-031, nothing ever asked the merchant again after
    confirmation, so this change was structurally invisible to every gate.
    """

    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def add_to_cart(self, variant_id, quantity, cart_id=None):
        # Only its cart_id is used by the caller (select_offer immediately
        # re-reads via read_cart) — this must not itself count as a read.
        return ObservedCart(
            merchant="slurrpfarm.com", cart_id="cart-1",
            lines=[CartLine(sku=variant_id, variant_id=variant_id, quantity=quantity, unit_price_paise=6600)],
            total_paise=quantity * 6600,
        )

    async def read_cart(self, cart_id):
        type(self).calls += 1
        quantity = 2 if type(self).calls == 1 else 5   # confirmed 2, mutates to 5
        return ObservedCart(
            merchant="slurrpfarm.com", cart_id="cart-1",
            lines=[CartLine(sku="v1", variant_id="v1", quantity=quantity, unit_price_paise=6600)],
            total_paise=quantity * 6600,
        )


def test_a_merchant_side_cart_change_after_confirmation_is_blocked_not_paid(client, monkeypatch):
    """The regression test for F-031. Before the fix, ``create_payment_order``
    evaluated the gates against the snapshot taken at confirmation and never
    asked the merchant again — this exact scenario would have returned
    ``gates.allow=True`` and created a real Razorpay order for 5 units after
    the user had only confirmed 2. It must not.
    """
    _MutatingAdapter.calls = 0
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _MutatingAdapter)

    session_id = _confirmed_session(client)
    assert _MutatingAdapter.calls == 1   # confirmation saw the honest 2-unit cart

    response = client.post(f"/api/sessions/{session_id}/payment/order")

    assert _MutatingAdapter.calls == 2   # payment re-read the merchant, and caught the change
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "G_CONFIRMATION_MATCHES" in detail["failed_gates"]
    assert _FakeRazorpayClient.create_calls == 0   # never reached Razorpay


# --- PAYMENT_UNKNOWN: a create_order call whose response is lost -----------

class _TimeoutThenFoundClient:
    """create_order raises, as if the request timed out. find_order_by_receipt
    then reveals Razorpay actually made the order anyway — the response was
    lost, not the request."""

    def __init__(self, key_id, key_secret):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def create_order(self, *, amount_paise, currency, receipt, notes):
        from orderguard.razorpay_client import RazorpayError
        raise RazorpayError("simulated timeout — no response received")

    async def find_order_by_receipt(self, receipt):
        return {"id": FAKE_ORDER_ID, "receipt": receipt}


class _TimeoutThenNotFoundClient(_TimeoutThenFoundClient):
    """The other honest outcome: Razorpay genuinely never got the request."""

    async def find_order_by_receipt(self, receipt):
        return None


def test_a_lost_response_that_was_actually_created_resolves_to_success(client, monkeypatch):
    """The response was lost; the order was not. This must not become a
    second real order on retry, and must not be reported as a hard failure —
    the receipt lookup proves it already exists."""
    monkeypatch.setattr(executor, "RazorpayClient", _TimeoutThenFoundClient)
    session_id = _confirmed_session(client)

    response = client.post(f"/api/sessions/{session_id}/payment/order")

    assert response.status_code == 200
    body = response.json()
    assert body["razorpay_order_id"] == FAKE_ORDER_ID
    assert body["authorization"] is not None

    from orderguard.app import LEDGER
    from orderguard.ledger import LedgerStatus, get_entry
    session = app_module._SESSIONS[session_id]
    key = f"{session.intent.merchant}|{session.intent.intent_id}|purchase|{session.intent.confirmed_cart_hash}"
    assert get_entry(LEDGER, key).status is LedgerStatus.PENDING   # resolved, not stuck UNKNOWN


def test_a_lost_response_that_was_genuinely_never_created_is_reported_and_retryable(client, monkeypatch):
    monkeypatch.setattr(executor, "RazorpayClient", _TimeoutThenNotFoundClient)
    session_id = _confirmed_session(client)

    response = client.post(f"/api/sessions/{session_id}/payment/order")
    assert response.status_code == 502

    from orderguard.app import LEDGER
    from orderguard.ledger import LedgerStatus, get_entry
    session = app_module._SESSIONS[session_id]
    key = f"{session.intent.merchant}|{session.intent.intent_id}|purchase|{session.intent.confirmed_cart_hash}"
    entry = get_entry(LEDGER, key)
    # resolved back to PENDING with no order attached — a real retry can proceed
    assert entry.status is LedgerStatus.PENDING
    assert entry.razorpay_order_id == ""

    # and a real retry, once Razorpay is reachable again, succeeds cleanly
    monkeypatch.setattr(executor, "RazorpayClient", _FakeRazorpayClient)
    retried = client.post(f"/api/sessions/{session_id}/payment/order")
    assert retried.status_code == 200
    assert retried.json()["razorpay_order_id"] == FAKE_ORDER_ID


def test_an_unresolvable_timeout_never_lies_about_success(client, monkeypatch):
    """Even the RESOLUTION attempt fails (Razorpay still unreachable). The row
    stays UNKNOWN, and the caller is told exactly that — never a false 200."""
    class _AlwaysDownClient:
        def __init__(self, key_id, key_secret):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def create_order(self, *, amount_paise, currency, receipt, notes):
            from orderguard.razorpay_client import RazorpayError
            raise RazorpayError("simulated timeout")

        async def find_order_by_receipt(self, receipt):
            from orderguard.razorpay_client import RazorpayError
            raise RazorpayError("still unreachable")

    monkeypatch.setattr(executor, "RazorpayClient", _AlwaysDownClient)
    session_id = _confirmed_session(client)

    response = client.post(f"/api/sessions/{session_id}/payment/order")
    assert response.status_code == 502

    from orderguard.app import LEDGER
    from orderguard.ledger import LedgerStatus, get_entry
    session = app_module._SESSIONS[session_id]
    key = f"{session.intent.merchant}|{session.intent.intent_id}|purchase|{session.intent.confirmed_cart_hash}"
    assert get_entry(LEDGER, key).status is LedgerStatus.UNKNOWN   # honestly still uncertain


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

    # the signed authorization behind this payment was consumed exactly once,
    # by the same single-use mechanism as the ledger row above
    from orderguard.authorization import get_consumption
    consumption = get_consumption(app_module.AUTH_DB, session.authorization.authorization_id)
    assert consumption is not None
    assert consumption.razorpay_order_id == order["razorpay_order_id"]


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


def test_gates_passed_lists_all_thirteen_gate_names_not_just_a_count(client):
    """Real, reproduced gap: the buy flow only ever showed "13/13 gates
    passed" as one opaque number -- no way to see WHICH checks actually
    ran. The full named checklist must be in the response, not just a
    count, so the UI can render a real per-gate CI-style list."""
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    assert len(order["gate_names_passed"]) == 13
    assert order["gate_names_failed"] == []
    assert "G_PRICES_MATCH" in order["gate_names_passed"]
    assert "G_WITHIN_CAP" in order["gate_names_passed"]


def test_a_successful_verify_reports_all_nine_post_payment_gate_names(client):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    payment_id = "pay_real_00002"
    signature = _sign(order["razorpay_order_id"], payment_id)

    result = client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": payment_id, "razorpay_signature": signature,
    }).json()

    assert result["captured"] is True
    assert result["gates_passed"] == result["gates_total"] == 9
    assert len(result["gate_names_passed"]) == 9
    assert "G_NO_REFUND" in result["gate_names_passed"]
    assert "G_SINGLE_CANDIDATE" in result["gate_names_passed"]


def test_a_refunded_payment_is_refused_not_captured(client, monkeypatch):
    """Regression for the real gap found in G_NO_REFUND: previously nothing
    anywhere checked whether a "captured" payment had a refund against it —
    this proves it now actually blocks capture, not just labels one."""
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    monkeypatch.setattr(_FakeRazorpayClient, "refunded_amount", 6600)
    payment_id = "pay_refunded_00001"
    signature = _sign(order["razorpay_order_id"], payment_id)

    result = client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": payment_id, "razorpay_signature": signature,
    }).json()

    assert result["captured"] is False
    assert "refund" in result["reason"].lower()
    assert "G_NO_REFUND" in result["gate_names_failed"]

    from orderguard.app import LEDGER
    session = app_module._SESSIONS[session_id]
    intent = session.intent
    key = f"{intent.merchant}|{intent.intent_id}|purchase|{intent.confirmed_cart_hash}"
    entry = get_entry(LEDGER, key)
    assert entry.status is not LedgerStatus.CAPTURED


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


# --- the Razorpay webhook: server-to-server payment truth -------------------

def _webhook_body(order_id, payment_id, amount, event="payment.captured", currency="INR") -> bytes:
    import json
    return json.dumps({
        "entity": "event", "event": event,
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "status": "captured",
            "amount": amount, "currency": currency,
        }}},
    }).encode()


def _webhook_signature(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_a_valid_webhook_captures_the_payment_and_runs_side_effects(client):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    body = _webhook_body(order["razorpay_order_id"], "pay_via_webhook", 13200)

    response = client.post("/api/webhooks/razorpay", content=body, headers={
        "x-razorpay-signature": _webhook_signature(body), "x-razorpay-event-id": "evt_1",
    })
    assert response.status_code == 200

    from orderguard.app import LEDGER, MEMORY
    session = app_module._SESSIONS[session_id]
    key = f"{session.intent.merchant}|{session.intent.intent_id}|purchase|{session.intent.confirmed_cart_hash}"
    entry = get_entry(LEDGER, key)
    assert entry.status is LedgerStatus.CAPTURED
    assert entry.razorpay_payment_id == "pay_via_webhook"
    assert len(recent_orders(MEMORY, "buyer1")) == 1   # side effects ran, same as the client path

    from orderguard.authorization import get_consumption
    from orderguard.app import AUTH_DB
    assert get_consumption(AUTH_DB, session.authorization.authorization_id) is not None


def test_an_invalid_signature_is_rejected_before_anything_is_parsed(client):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    body = _webhook_body(order["razorpay_order_id"], "pay_x", 13200)

    response = client.post("/api/webhooks/razorpay", content=body, headers={
        "x-razorpay-signature": "0" * 64, "x-razorpay-event-id": "evt_bad_sig",
    })
    assert response.status_code == 400

    from orderguard.app import LEDGER
    session = app_module._SESSIONS[session_id]
    key = f"{session.intent.merchant}|{session.intent.intent_id}|purchase|{session.intent.confirmed_cart_hash}"
    assert get_entry(LEDGER, key).status is LedgerStatus.PENDING   # untouched


def test_a_duplicate_delivery_is_a_no_op_not_an_error(client):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    body = _webhook_body(order["razorpay_order_id"], "pay_dup", 13200)
    headers = {"x-razorpay-signature": _webhook_signature(body), "x-razorpay-event-id": "evt_dup"}

    first = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    second = client.post("/api/webhooks/razorpay", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "duplicate" in second.json()["note"]

    from orderguard.app import MEMORY
    assert len(recent_orders(MEMORY, "buyer1")) == 1   # side effects still ran only once


def test_an_event_for_an_unknown_order_is_rejected(client):
    body = _webhook_body("order_never_created_here", "pay_x", 13200)
    response = client.post("/api/webhooks/razorpay", content=body, headers={
        "x-razorpay-signature": _webhook_signature(body), "x-razorpay-event-id": "evt_unknown",
    })
    assert response.status_code == 404


def test_the_client_path_winning_first_makes_the_webhook_a_clean_no_op(client):
    """Whichever channel reports capture first, the other must not double-write
    order history or re-consume the authorization."""
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()

    payment_id = "pay_client_won"
    signature = _sign(order["razorpay_order_id"], payment_id)
    client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": payment_id, "razorpay_signature": signature,
    })

    body = _webhook_body(order["razorpay_order_id"], payment_id, 13200)
    response = client.post("/api/webhooks/razorpay", content=body, headers={
        "x-razorpay-signature": _webhook_signature(body), "x-razorpay-event-id": "evt_after_client",
    })
    assert response.status_code == 200
    assert "already captured" in response.json().get("note", "")

    from orderguard.app import MEMORY
    assert len(recent_orders(MEMORY, "buyer1")) == 1   # exactly once, not twice


def test_a_non_payment_event_is_acknowledged_without_action(client):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    body = _webhook_body(order["razorpay_order_id"], "pay_x", 13200, event="order.paid")

    response = client.post("/api/webhooks/razorpay", content=body, headers={
        "x-razorpay-signature": _webhook_signature(body), "x-razorpay-event-id": "evt_order_paid",
    })
    assert response.status_code == 200
    assert "no action taken" in response.json()["note"]
