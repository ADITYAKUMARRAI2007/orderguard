"""GET /api/sessions/{id}/receipt — assembles gates, signed authorization,
Razorpay ledger state and the audit chain into one artifact. Invents no new
source of truth: every field is read from checkout_guard/authorization/
ledger/audit, recomputed live rather than trusted from a cached flag.

Reuses test_payment_flow.py's fixtures/fakes directly — this endpoint reads
the exact same state that file already proves is correct.
"""

from test_payment_flow import (  # noqa: F401 — pytest fixture
    FAKE_ORDER_ID, _FakeRazorpayClient, _confirmed_session, _sign, client,
)

from orderguard import app as app_module


def test_a_never_confirmed_session_reports_not_confirmed(client):
    session_id = client.post("/api/sessions", json={
        "user_id": "buyer1",
        "request_text": "slurrpfarm.com: two litres of milk, budget 500 rupees",
    }).json()["session_id"]

    receipt = client.get(f"/api/sessions/{session_id}/receipt").json()

    assert receipt["status"] == "NOT_CONFIRMED"
    assert receipt["gates"]["evaluated"] is False
    assert receipt["authorization"] is None
    assert receipt["payment"] is None


def test_a_confirmed_but_unpaid_session_shows_passed_gates_and_a_verifiable_authorization(client):
    session_id = _confirmed_session(client)
    client.post(f"/api/sessions/{session_id}/payment/order")

    receipt = client.get(f"/api/sessions/{session_id}/receipt").json()

    assert receipt["status"] == "AWAITING_PAYMENT"
    assert receipt["gates"]["evaluated"] is True
    assert receipt["gates"]["allow"] is True
    assert len(receipt["gates"]["passed"]) == 13
    assert receipt["gates"]["failed"] == []

    auth = receipt["authorization"]
    assert auth["signature_valid"] is True
    assert auth["expired"] is False
    assert auth["consumed"] is False
    assert auth["amount_paise"] == 13200

    assert receipt["payment"]["status"] == "pending"
    assert receipt["audit"]["verified"] is True


def test_a_captured_payment_shows_paid_and_a_consumed_authorization(client):
    session_id = _confirmed_session(client)
    order = client.post(f"/api/sessions/{session_id}/payment/order").json()
    payment_id = "pay_receipt_001"
    client.post(f"/api/sessions/{session_id}/payment/verify", json={
        "razorpay_payment_id": payment_id,
        "razorpay_signature": _sign(order["razorpay_order_id"], payment_id),
    })

    receipt = client.get(f"/api/sessions/{session_id}/receipt").json()

    assert receipt["status"] == "PAID"
    assert receipt["payment"]["status"] == "captured"
    assert receipt["payment"]["razorpay_payment_id"] == payment_id
    assert receipt["payment"]["captured_amount_paise"] == _FakeRazorpayClient.captured_amount
    assert receipt["authorization"]["consumed"] is True


def test_a_blocked_purchase_reports_blocked_with_the_failed_gate_named_and_no_authorization(client, monkeypatch):
    """Regression companion to F-031's payment-flow test: a merchant-side
    cart mutation after confirmation must show up on the receipt as BLOCKED,
    naming the real failed gate — never silently omitted, never shown as if
    it had been paid."""
    from test_payment_flow import _MutatingAdapter
    _MutatingAdapter.calls = 0
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _MutatingAdapter)

    session_id = _confirmed_session(client)
    blocked = client.post(f"/api/sessions/{session_id}/payment/order")
    assert blocked.status_code == 409

    receipt = client.get(f"/api/sessions/{session_id}/receipt").json()

    assert receipt["status"] == "BLOCKED"
    assert receipt["gates"]["evaluated"] is True
    assert receipt["gates"]["allow"] is False
    assert "G_CONFIRMATION_MATCHES" in receipt["gates"]["failed"]
    assert "G_CONFIRMATION_MATCHES" in receipt["gates"]["reasons"]
    assert receipt["authorization"] is None
    assert receipt["payment"] is None


def test_an_unknown_session_is_404(client):
    response = client.get("/api/sessions/does-not-exist/receipt")
    assert response.status_code == 404
