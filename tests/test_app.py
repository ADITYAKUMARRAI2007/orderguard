"""The guarded app flow, tested without live merchants or a model key."""

from fastapi.testclient import TestClient

from orderguard import app as app_module
from orderguard.commerce import Offer, ScoredOffer, SearchOutcome
from orderguard.llm import StubProvider
from orderguard.models import CartLine, ObservedCart


def _outcome() -> SearchOutcome:
    offer = Offer(
        store="freshcart", store_label="FreshCart", product_id="p1",
        variant_id="v1", title="Milk", price_minor=6600, currency="INR", available=True,
    )
    return SearchOutcome(
        query="milk", quantity=2, budget_minor=50000,
        offers=[ScoredOffer(
            offer=offer, relevance=1.0, in_stock=True, priced=True,
            within_budget=True, line_total_minor=13200,
        )],
        stores_searched=["FreshCart"],
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
            merchant="freshcart", cart_id="cart-1",
            lines=[CartLine(sku=variant_id, variant_id=variant_id, quantity=quantity, unit_price_paise=6600)],
            total_paise=quantity * 6600,
        )

    async def read_cart(self, cart_id):
        return await self.add_to_cart("v1", 2, cart_id)


class _OneItemProvider:
    name = "test"

    def complete(self, system, user, schema):
        return {
            "merchant": "freshcart",
            "items": [{"requested_product": "milk", "quantity": 2, "unit": "litre"}],
            "maximum_total_paise": 50000,
        }


def test_live_shaped_flow_requires_search_then_explicit_selection(monkeypatch):
    app_module._SESSIONS.clear()
    monkeypatch.setattr(app_module, "provider_from_env", lambda: StubProvider())

    async def search(*args, **kwargs):
        return _outcome()

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _Adapter)
    client = TestClient(app_module.app)

    created = client.post("/api/sessions", json={
        "user_id": "u1",
        "request_text": "freshcart: two litres of milk and six bananas, budget 500 rupees",
    }).json()
    session_id = created["session_id"]

    assert client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "freshcart|v1", "explicit_user_selection": True,
    }).status_code == 409

    searched = client.post(f"/api/sessions/{session_id}/items/0/search")
    assert searched.status_code == 200

    selected = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "freshcart|v1", "explicit_user_selection": True,
    })
    assert selected.status_code == 200
    assert selected.json()["observed_cart"]["cart_id"] == "cart-1"

    # The second requested item is deliberately not auto-selected, so the app
    # refuses confirmation rather than manufacturing a two-item cart.
    assert client.post(f"/api/sessions/{session_id}/confirm").status_code == 409


def test_complete_single_item_flow_freezes_a_verified_cart(monkeypatch):
    app_module._SESSIONS.clear()
    monkeypatch.setattr(app_module, "provider_from_env", _OneItemProvider)

    async def search(*args, **kwargs):
        return _outcome()

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _Adapter)
    client = TestClient(app_module.app)

    session_id = client.post("/api/sessions", json={"user_id": "u1", "request_text": "two litres milk"}).json()["session_id"]
    assert client.post(f"/api/sessions/{session_id}/items/0/search").status_code == 200
    assert client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "freshcart|v1", "explicit_user_selection": True,
    }).status_code == 200

    confirmed = client.post(f"/api/sessions/{session_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["intent"]["status"] == "confirmed"
    assert confirmed.json()["intent"]["confirmed_cart_hash"]


def test_a_store_the_user_named_is_enforced_at_selection(monkeypatch):
    """"Get me coffee from FreshCart" must not quietly buy from somewhere else.

    Naming a store is a constraint, not a hint. The search may still surface
    offers from elsewhere — that is useful for comparison — but selecting one
    has to be refused.
    """
    app_module._SESSIONS.clear()
    monkeypatch.setattr(app_module, "provider_from_env", lambda: StubProvider())

    elsewhere = Offer(
        store="othershop.example", store_label="Other Shop", product_id="p9",
        variant_id="v9", title="Milk", price_minor=5000, currency="INR", available=True,
    )

    async def search(*args, **kwargs):
        outcome = _outcome()
        outcome.offers.append(ScoredOffer(
            offer=elsewhere, relevance=1.0, in_stock=True, priced=True,
            within_budget=True, line_total_minor=10000,
        ))
        return outcome

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _Adapter)
    client = TestClient(app_module.app)

    session_id = client.post("/api/sessions", json={
        "user_id": "u1",
        "request_text": "freshcart: two litres of milk and six bananas, budget 500 rupees",
    }).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/items/0/search")

    refused = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "othershop.example|v9", "explicit_user_selection": True,
    })
    assert refused.status_code == 409
    assert "freshcart" in refused.json()["detail"].lower()

    allowed = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "freshcart|v1", "explicit_user_selection": True,
    })
    assert allowed.status_code == 200
