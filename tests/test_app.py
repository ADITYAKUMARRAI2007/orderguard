"""The guarded app flow, tested without live merchants or a model key."""

import pytest
from fastapi.testclient import TestClient

from orderguard import app as app_module
from orderguard.commerce import Offer, ScoredOffer, SearchOutcome
from orderguard.llm import StubProvider
from orderguard.memory import memory_engine
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


# --- connectors -------------------------------------------------------------

def test_the_connector_directory_shows_what_we_cannot_use(monkeypatch):
    """A directory listing only what works would be a sales page.

    The blocked entries are the informative ones: Swiggy is real and answered
    401, Zomato's terms forbid us, Zepto has no public surface at all.
    """
    client = TestClient(app_module.app)
    body = client.get("/api/connectors").json()
    found = {c["id"]: c for c in body["connectors"]}

    assert found["swiggy"]["status"] == "needs_access"
    assert "401" in found["swiggy"]["evidence"]
    assert found["zomato"]["status"] == "restricted"
    assert found["zepto"]["status"] == "unavailable"

    # and nothing in the directory claims it can place a third-party order
    assert all(not c["can_order"] for c in body["connectors"] if c["id"] != "razorpay")


# --- memory -----------------------------------------------------------------

@pytest.fixture
def memory_client(monkeypatch):
    """A client whose memory lives in RAM, never in data/memory.db."""
    monkeypatch.setattr(app_module, "MEMORY", memory_engine(":memory:"))
    app_module._SESSIONS.clear()
    return TestClient(app_module.app)


def test_a_spending_limit_cannot_be_stored_through_the_api(memory_client):
    """The most important refusal in the product, checked at the edge.

    A preference that could carry a budget would let anything able to write a
    preference decide what the agent may spend.
    """
    refused = memory_client.post(
        "/api/memory/u1/preferences", json={"key": "budget", "value": "999999"}
    )
    assert refused.status_code == 422
    assert "never remembered" in refused.json()["detail"]

    assert memory_client.get("/api/memory/u1").json()["preferences"] == {}


def test_a_remembered_store_narrows_the_search_and_says_so(memory_client, monkeypatch):
    """Memory doing real work — and admitting it.

    Without the preference the search fans out across every grocery store. With
    it, one store is searched, and the session carries a sentence saying which
    remembered value was used.

    The request names no store, which is the case memory is for. A store named
    in the request would win instead.
    """
    seen: list[tuple] = []

    async def search(*args, **kwargs):
        seen.append(kwargs.get("stores"))
        return _outcome()

    # A request with no merchant in it at all.
    stub = StubProvider(extra_answers={
        "one pack of coffee, budget 900 rupees": {
            "items": [{"requested_product": "coffee", "quantity": 1, "unit": "pack"}],
            "maximum_total_paise": 90000,
        }
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)
    monkeypatch.setattr(app_module, "search_stores", search)

    memory_client.post(
        "/api/memory/u1/preferences", json={"key": "store", "value": "Blue Tokai"}
    )
    session = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "one pack of coffee, budget 900 rupees",
    }).json()

    assert session["intent"]["merchant"] == ""          # they named no store
    assert session["memory_notes"] == ["Using your usual store: Blue Tokai."]

    assert memory_client.post(
        f"/api/sessions/{session['session_id']}/items/0/search"
    ).status_code == 200
    assert [s.label for s in seen[0]] == ["Blue Tokai"]


def test_the_conversation_is_remembered_across_a_reload(memory_client):
    session_id = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "millet cereal, budget 700 rupees",
    }).json()["session_id"]
    memory_client.post(
        f"/api/sessions/{session_id}/messages", json={"message": "make it two packs"}
    )

    turns = memory_client.get(f"/api/sessions/{session_id}/history").json()["turns"]
    assert [t["text"] for t in turns] == [
        "millet cereal, budget 700 rupees",
        "make it two packs",
    ]


def test_a_user_can_delete_everything_we_hold(memory_client):
    memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "millet cereal, budget 700 rupees",
    })
    memory_client.post(
        "/api/memory/u1/preferences", json={"key": "brand", "value": "Slurrp Farm"}
    )

    deleted = memory_client.delete("/api/memory/u1").json()["deleted"]
    assert deleted["Preference"] == 1
    assert deleted["ChatTurn"] >= 1

    remaining = memory_client.get("/api/memory/u1").json()
    assert remaining["preferences"] == {}
    assert remaining["recent_orders"] == []


def test_memory_never_offers_a_purchase_only_a_suggestion(memory_client):
    """An empty history must not produce something a cart could swallow."""
    body = memory_client.get("/api/memory/u1").json()
    assert body["reorder_suggestion"] is None
    assert "raise a spending limit" in body["note"]
