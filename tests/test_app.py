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


class _OneItemProvider:
    name = "test"

    def complete(self, system, user, schema):
        return {
            "merchant": "slurrpfarm.com",
            "items": [{"requested_product": "milk", "quantity": 2, "unit": "litre"}],
            "maximum_total_paise": 50000,
        }


def test_live_shaped_flow_requires_search_then_explicit_selection(monkeypatch):
    app_module._SESSIONS.clear()
    # Not the bare StubProvider(): its built-in default fixtures are keyed on
    # a request literally containing "freshcart", which after wiring the real
    # FreshCartAdapter would route this test into a live HTTP call instead of
    # the mocked _Adapter it is meant to exercise.
    stub = StubProvider(extra_answers={
        "slurrpfarm.com: two litres of milk and six bananas, budget 500 rupees": {
            "merchant": "slurrpfarm.com",
            "items": [
                {"requested_product": "milk", "quantity": 2, "unit": "litre"},
                {"requested_product": "banana", "quantity": 6, "unit": "piece"},
            ],
            "maximum_total_paise": 50000,
        },
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    async def search(*args, **kwargs):
        return _outcome()

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _Adapter)
    client = TestClient(app_module.app)

    created = client.post("/api/sessions", json={
        "user_id": "u1",
        "request_text": "slurrpfarm.com: two litres of milk and six bananas, budget 500 rupees",
    }).json()
    session_id = created["session_id"]

    assert client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
    }).status_code == 409

    searched = client.post(f"/api/sessions/{session_id}/items/0/search")
    assert searched.status_code == 200

    selected = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
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
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
    }).status_code == 200

    confirmed = client.post(f"/api/sessions/{session_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["intent"]["status"] == "confirmed"
    assert confirmed.json()["intent"]["confirmed_cart_hash"]


def test_search_results_carry_an_advisory_council_recommendation(monkeypatch):
    """The wiring, not just the module: decision_council actually runs inside
    a real search response. With no Fit/Critic answers registered, the stub
    can't answer either question — proving the live app falls back safely
    (fallback_used=True) rather than the endpoint silently skipping the
    council or crashing when the model has nothing to say."""
    app_module._SESSIONS.clear()
    monkeypatch.setattr(app_module, "provider_from_env", _OneItemProvider)

    async def search(*args, **kwargs):
        outcome = _outcome()
        outcome.offers.append(ScoredOffer(
            offer=Offer(
                store="othershop.example", store_label="Other Shop", product_id="p9",
                variant_id="v9", title="Milk", price_minor=6000, currency="INR",
            ),
            relevance=1.0, in_stock=True, priced=True, within_budget=True,
            line_total_minor=12000,
        ))
        return outcome

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _Adapter)
    client = TestClient(app_module.app)

    session_id = client.post("/api/sessions", json={"user_id": "u1", "request_text": "two litres milk"}).json()["session_id"]
    result = client.post(f"/api/sessions/{session_id}/items/0/search").json()

    assert result["council"] is not None
    assert result["council"]["alternatives_considered"] == 2
    assert result["council"]["fallback_used"] is True
    assert result["council"]["recommended_id"] in {"slurrpfarm.com|v1", "othershop.example|v9"}


def test_a_store_the_user_named_is_enforced_at_selection(monkeypatch):
    """"Get me coffee from Slurrp Farm" must not quietly buy from somewhere else.

    Naming a store is a constraint, not a hint. The search may still surface
    offers from elsewhere — that is useful for comparison — but selecting one
    has to be refused.
    """
    app_module._SESSIONS.clear()
    stub = StubProvider(extra_answers={
        "slurrpfarm.com: two litres of milk and six bananas, budget 500 rupees": {
            "merchant": "slurrpfarm.com",
            "items": [
                {"requested_product": "milk", "quantity": 2, "unit": "litre"},
                {"requested_product": "banana", "quantity": 6, "unit": "piece"},
            ],
            "maximum_total_paise": 50000,
        },
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

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
        "request_text": "slurrpfarm.com: two litres of milk and six bananas, budget 500 rupees",
    }).json()["session_id"]
    client.post(f"/api/sessions/{session_id}/items/0/search")

    refused = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "othershop.example|v9", "explicit_user_selection": True,
    })
    assert refused.status_code == 409
    assert "slurrpfarm.com" in refused.json()["detail"].lower()

    allowed = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
    })
    assert allowed.status_code == 200


# --- connectors -------------------------------------------------------------

def test_the_connector_directory_shows_what_we_cannot_use(monkeypatch):
    """A directory listing only what works would be a sales page.

    The blocked entries are the informative ones: Zomato's terms forbid us,
    Zepto has no public surface at all. Swiggy used to be in this category
    (401, no credentials) until it was actually connected on 2026-08-29 —
    see test_connectors.py for that story now.
    """
    client = TestClient(app_module.app)
    body = client.get("/api/connectors").json()
    found = {c["id"]: c for c in body["connectors"]}

    assert found["swiggy-instamart"]["evidence"] == "connector_verified"
    assert found["zomato"]["evidence"] == "restricted"
    assert found["zepto"]["evidence"] == "unavailable"

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


# --- shopping at a store nobody integrated ---------------------------------

def test_a_store_the_user_names_can_be_added_and_is_then_searched(
    memory_client, monkeypatch
):
    """The store list grows by use, not by us maintaining one.

    Twenty Indian D2C brands were tried at random and ten answered, none of
    which had been integrated. So "which stores work" is a question we ask a
    domain, not a list we keep.
    """
    from orderguard.commerce.discovery import StoreCapability

    async def fake_discover(domain, client=None):
        return StoreCapability(
            domain="farmley.com", reachable=True,
            tools=("search_catalog", "update_cart", "get_cart"),
            can_search=True, can_cart=True,
        )

    monkeypatch.setattr(app_module, "discover", fake_discover)

    added = memory_client.post(
        "/api/users/u1/stores", json={"domain": "https://farmley.com/collections/all"}
    ).json()
    assert added["shoppable"] and added["saved"]
    assert added["domain"] == "farmley.com"

    listed = memory_client.get("/api/users/u1/stores").json()
    assert [s["domain"] for s in listed["added_by_you"]] == ["farmley.com"]

    seen: list = []

    async def search(*args, **kwargs):
        seen.append([s.label for s in kwargs.get("stores")])
        return _outcome()

    stub = StubProvider(extra_answers={
        "one pack of cashews, budget 900 rupees": {
            "items": [{"requested_product": "cashews", "quantity": 1, "unit": "pack"}],
            "maximum_total_paise": 90000,
        }
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)
    monkeypatch.setattr(app_module, "search_stores", search)

    session_id = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "one pack of cashews, budget 900 rupees",
    }).json()["session_id"]
    memory_client.post(f"/api/sessions/{session_id}/items/0/search")

    assert "Farmley" in seen[0]          # searched alongside ours


def test_a_store_that_cannot_take_a_cart_is_not_saved(memory_client, monkeypatch):
    """Browsable is not buyable. Two real stores answered with search only."""
    from orderguard.commerce.discovery import StoreCapability

    async def fake_discover(domain, client=None):
        return StoreCapability(
            domain="minimalist.co", reachable=True, tools=("search_catalog",),
            can_search=True, can_cart=False,
        )

    monkeypatch.setattr(app_module, "discover", fake_discover)

    result = memory_client.post(
        "/api/users/u1/stores", json={"domain": "minimalist.co"}
    ).json()

    assert result["shoppable"] is False
    assert result["saved"] is False
    assert "no cart" in result["message"]
    assert memory_client.get("/api/users/u1/stores").json()["added_by_you"] == []


def test_a_store_name_cannot_be_pointed_at_our_own_network(memory_client):
    """The reason is passed through, so it does not look like the shop is down.

    "Shop at 169.254.169.254" would otherwise make OrderGuard fetch cloud
    credentials on the caller's behalf.
    """
    refused = memory_client.post(
        "/api/users/u1/stores",
        json={"domain": "http://169.254.169.254/latest/meta-data/"},
    )
    assert refused.status_code == 422
    assert "not a shop domain" in refused.json()["detail"]

    for hostile in ("localhost:8000", "127.0.0.1", "192.168.0.1", "shop.internal"):
        assert memory_client.post(
            "/api/users/u1/stores", json={"domain": hostile}
        ).status_code == 422


def test_forgetting_everything_also_forgets_added_stores(memory_client, monkeypatch):
    from orderguard.commerce.discovery import StoreCapability

    async def fake_discover(domain, client=None):
        return StoreCapability("farmley.com", True, ("search_catalog", "update_cart",
                               "get_cart"), True, True)

    monkeypatch.setattr(app_module, "discover", fake_discover)
    memory_client.post("/api/users/u1/stores", json={"domain": "farmley.com"})

    assert memory_client.delete("/api/memory/u1").json()["deleted"]["SavedStore"] == 1
    assert memory_client.get("/api/users/u1/stores").json()["added_by_you"] == []


def test_web_results_are_offered_separately_from_things_you_can_buy(
    memory_client, monkeypatch
):
    """Two lists, never one.

    Store offers can become a cart line. Web results are a claimed price and a
    link. Merging them would be the first step towards treating them the same.
    """
    from orderguard.websearch import StubSearchProvider, search_web as real_search

    stub = StubProvider(extra_answers={
        "one pack of cashews, budget 900 rupees": {
            "items": [{"requested_product": "cashews", "quantity": 1, "unit": "pack"}],
            "maximum_total_paise": 90000,
        }
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    async def web(query, **kwargs):
        return await real_search(query, provider=StubSearchProvider())

    monkeypatch.setattr(app_module, "search_web", web)

    session_id = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "one pack of cashews, budget 900 rupees",
    }).json()["session_id"]

    found = memory_client.post(f"/api/sessions/{session_id}/items/0/web").json()

    assert {r["site_label"] for r in found["results"]} == {"Amazon", "Flipkart"}
    # cheapest first now that a budget can be applied
    assert found["results"][0]["claimed_price_paise"] == 28900
    # nothing in a web result can be selected
    assert "variant_id" not in found["results"][0]


def test_no_search_key_leaves_store_shopping_untouched(memory_client, monkeypatch):
    stub = StubProvider(extra_answers={
        "one pack of cashews, budget 900 rupees": {
            "items": [{"requested_product": "cashews", "quantity": 1, "unit": "pack"}],
            "maximum_total_paise": 90000,
        }
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)
    monkeypatch.delenv("SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)

    async def search(*args, **kwargs):
        return _outcome()

    monkeypatch.setattr(app_module, "search_stores", search)

    session_id = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "one pack of cashews, budget 900 rupees",
    }).json()["session_id"]

    web = memory_client.post(f"/api/sessions/{session_id}/items/0/web").json()
    assert web["results"] == []
    assert "Store search works without it" in web["unavailable_reason"]

    stores = memory_client.post(f"/api/sessions/{session_id}/items/0/search")
    assert stores.status_code == 200          # unaffected


def test_an_item_no_shop_stocks_falls_back_to_the_web(memory_client, monkeypatch):
    """F-021: momos, onions and eggs returned a blank panel every time.

    The five shops we can buy from are speciality D2C brands. They will never
    stock an onion. Saying which shops were tried, and where the web says to get
    it, is an answer; an empty panel is not.
    """
    from orderguard.commerce.search import SearchOutcome
    from orderguard.websearch import StubSearchProvider, search_web as real_search

    stub = StubProvider(extra_answers={
        "one kg onion, budget 100 rupees": {
            "items": [{"requested_product": "onion", "quantity": 1, "unit": "kg"}],
            "maximum_total_paise": 10000,
        }
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    async def nothing(*args, **kwargs):
        return SearchOutcome(
            query="onion", quantity=1,
            stores_searched=["Slurrp Farm", "Blue Tokai"],
        )

    async def web(query, **kwargs):
        return await real_search(query, provider=StubSearchProvider([
            {"title": "Onion 1 kg", "link": "https://doorkisan.example/p",
             "snippet": "₹80", "source": "Door Kisan"},
        ]))

    monkeypatch.setattr(app_module, "search_stores", nothing)
    monkeypatch.setattr(app_module, "search_web", web)

    session_id = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "one kg onion, budget 100 rupees",
    }).json()["session_id"]

    found = memory_client.post(f"/api/sessions/{session_id}/items/0/search").json()

    assert found["offers"] == []
    assert "Slurrp Farm" in found["explanation"]      # names what it tried
    assert "cannot add them to a cart" in found["explanation"]
    assert [w["site_label"] for w in found["web"]] == ["Door Kisan"]
    assert found["web"][0]["claimed_price_paise"] == 8000


def test_a_store_going_down_mid_write_is_a_clean_refusal_not_a_crash(
    memory_client, monkeypatch
):
    """F-028: before this, the exception from a dead store reached FastAPI
    unhandled — a raw 500 with a stack trace, no explanation, though nothing
    unsafe happened because nothing in the session was touched yet.

    Failing closed by accident is not the same claim as failing closed and
    saying so. Razor Dvara's README makes exactly this point about a
    serviceability backend dying mid-request, and this project did not yet
    have a test for the equivalent case.
    """
    from orderguard.commerce import AdapterError

    class _DeadAdapter:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def add_to_cart(self, variant_id, quantity, cart_id=None):
            raise AdapterError("farmley.com: HTTP 503")

    stub = StubProvider(extra_answers={
        "one pack of cashews, budget 900 rupees": {
            "items": [{"requested_product": "cashews", "quantity": 1, "unit": "pack"}],
            "maximum_total_paise": 90000,
        }
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    async def search(*args, **kwargs):
        return _outcome()

    monkeypatch.setattr(app_module, "search_stores", search)
    monkeypatch.setattr(app_module, "ShopifyMCPAdapter", _DeadAdapter)

    session_id = memory_client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "one pack of cashews, budget 900 rupees",
    }).json()["session_id"]
    memory_client.post(f"/api/sessions/{session_id}/items/0/search")

    response = memory_client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "slurrpfarm.com|v1", "explicit_user_selection": True,
    })

    assert response.status_code == 502
    assert "did not respond" in response.json()["detail"]
    assert "Nothing was changed" in response.json()["detail"]

    # and the session genuinely was not advanced by the failed attempt
    session = memory_client.get(f"/api/sessions/{session_id}").json()
    assert session["observed_cart"] is None
    assert session["selected_by_item"] == {}


# --- FreshCart: our own merchant, routed by name --------------------------

def test_naming_freshcart_routes_to_the_real_adapter_not_shopify(monkeypatch):
    """The one merchant Razorpay can honestly pay (D-020). Naming it must use
    FreshCartAdapter, never the generic Shopify multi-store search."""
    from orderguard.commerce.base import Offer as CommerceOffer

    app_module._SESSIONS.clear()
    stub = StubProvider(extra_answers={
        "freshcart: two litres of milk, budget 500 rupees": {
            "merchant": "freshcart",
            "items": [{"requested_product": "milk", "quantity": 2, "unit": "litre"}],
            "maximum_total_paise": 50000,
        },
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    shopify_was_called = False

    async def shopify_search_should_not_run(*args, **kwargs):
        nonlocal shopify_was_called
        shopify_was_called = True
        return _outcome()

    class _FakeFreshCart:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def search(self, query, limit=10, location=None):
            return [CommerceOffer(
                store="freshcart", store_label="FreshCart", product_id="milk_1l",
                variant_id="milk_1l", title="Amul Taaza Milk 1L", price_minor=6600,
                currency="INR", available=True,
            )]

        async def add_to_cart(self, variant_id, quantity, cart_id=None):
            return await self.read_cart(cart_id or "orderguard-session")

        async def read_cart(self, cart_id):
            return ObservedCart(
                merchant="freshcart", cart_id=cart_id,
                lines=[CartLine(sku="milk_1l", variant_id="milk_1l", quantity=2, unit_price_paise=6600)],
                subtotal_paise=13200, delivery_paise=3000, total_paise=16200,
            )

    monkeypatch.setattr(app_module, "search_stores", shopify_search_should_not_run)
    monkeypatch.setattr(app_module, "FreshCartAdapter", _FakeFreshCart)
    client = TestClient(app_module.app)

    session_id = client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "freshcart: two litres of milk, budget 500 rupees",
    }).json()["session_id"]

    out = client.post(f"/api/sessions/{session_id}/items/0/search").json()

    assert shopify_was_called is False
    assert out["stores_searched"] == ["FreshCart"]
    assert out["offers"][0]["offer"]["title"] == "Amul Taaza Milk 1L"

    selected = client.post(f"/api/sessions/{session_id}/items/0/select", json={
        "offer_key": "freshcart|milk_1l", "explicit_user_selection": True,
    })
    assert selected.status_code == 200
    assert selected.json()["observed_cart"]["total_paise"] == 16200


def test_freshcart_with_nothing_in_stock_explains_and_offers_the_web(monkeypatch):
    app_module._SESSIONS.clear()
    stub = StubProvider(extra_answers={
        "freshcart: caviar, budget 500 rupees": {
            "merchant": "freshcart",
            "items": [{"requested_product": "caviar", "quantity": 1, "unit": "unit"}],
            "maximum_total_paise": 50000,
        },
    })
    monkeypatch.setattr(app_module, "provider_from_env", lambda: stub)

    class _EmptyFreshCart:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def search(self, query, limit=10, location=None):
            return []

    monkeypatch.setattr(app_module, "FreshCartAdapter", _EmptyFreshCart)
    client = TestClient(app_module.app)

    session_id = client.post("/api/sessions", json={
        "user_id": "u1", "request_text": "freshcart: caviar, budget 500 rupees",
    }).json()["session_id"]

    out = client.post(f"/api/sessions/{session_id}/items/0/search").json()
    assert out["offers"] == []
    assert "FreshCart does not stock caviar" in out["explanation"]


# --- evidence screen endpoints -----------------------------------------------

def test_eval_results_reports_honestly_when_never_generated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # no results/latest.json here
    client = TestClient(app_module.app)
    body = client.get("/api/eval-results").json()
    assert body["generated_at"] is None
    assert "make eval" in body["note"]


def test_audit_verify_reports_a_healthy_empty_chain(monkeypatch):
    from orderguard.audit import audit_engine
    monkeypatch.setattr(app_module, "AUDIT", audit_engine(":memory:"))
    client = TestClient(app_module.app)

    body = client.get("/api/audit/verify").json()
    assert body["verified"] is True
    assert body["event_count"] == 0


# --- /api/ci-checks: the GitHub-Actions-style checks-run view --------------

def test_ci_checks_reports_pending_honestly_when_nothing_has_run_yet(monkeypatch, tmp_path):
    from orderguard.audit import audit_engine
    monkeypatch.setattr(app_module, "AUDIT", audit_engine(":memory:"))
    monkeypatch.chdir(tmp_path)   # no results/*.json here at all
    client = TestClient(app_module.app)

    body = client.get("/api/ci-checks").json()
    names = {c["name"]: c for c in body["checks"]}

    # The one check that always runs regardless of files on disk (it reads
    # the live audit engine, not a results/ file) reports real success.
    assert names["Audit chain integrity"]["status"] == "success"
    assert names["Audit chain integrity"]["summary"] == "0 events, hash chain intact"

    # A check with no artifact yet is reported as not-yet-run, never
    # silently omitted and never guessed at as passing or failing.
    assert names["Backend test suite"]["status"] == "pending"
    assert "make test-report" in names["Backend test suite"]["summary"]

    # Checks whose artifact simply doesn't exist yet are omitted, not
    # fabricated with zeros.
    assert "Fixed-fifty adversarial cart-integrity" not in names
    assert "Connector routing accuracy" not in names


def test_ci_checks_assembles_the_repos_own_real_artifacts(monkeypatch):
    """Run against the actual repo's results/ directory (no chdir) — proves
    the endpoint reads genuinely-written files, not fixtures, and that its
    pass/fail verdict per check matches what that file itself already says."""
    import json
    from pathlib import Path

    from orderguard.audit import audit_engine
    monkeypatch.setattr(app_module, "AUDIT", audit_engine(":memory:"))
    client = TestClient(app_module.app)

    body = client.get("/api/ci-checks").json()
    names = {c["name"]: c for c in body["checks"]}

    test_report_path = Path("results/test_report.json")
    if test_report_path.exists():
        on_disk = json.loads(test_report_path.read_text())
        assert names["Backend test suite"]["summary"] == on_disk["summary_line"]
        assert (names["Backend test suite"]["status"] == "success") == (on_disk["failed"] == 0)

    latest_path = Path("results/latest.json")
    if latest_path.exists():
        latest = json.loads(latest_path.read_text())
        agent_lab = latest.get("agent_attack_lab")
        if agent_lab:
            # Regression: this check's status/summary used to read a field
            # (false_match_rate) this fixture never has, so it silently
            # reported FAILURE on an all-correct run — checked explicitly.
            check = names["Agent-layer Attack Lab"]
            assert (check["status"] == "success") == bool(agent_lab["all_correct"])
            assert str(agent_lab["total"]) in check["summary"]

    # overall is failure if, and only if, at least one real check failed —
    # never independently computed.
    assert (body["overall"] == "failure") == any(c["status"] == "failure" for c in body["checks"])


def test_audit_verify_detects_a_real_tamper(monkeypatch):
    from orderguard.audit import AuditEvent, append_event, audit_engine
    from sqlmodel import Session, select

    engine = audit_engine(":memory:")
    append_event(engine, "test_event", {"a": 1})
    monkeypatch.setattr(app_module, "AUDIT", engine)

    with Session(engine) as db:
        row = db.exec(select(AuditEvent).where(AuditEvent.seq == 0)).one()
        row.payload_json = '{"a": 999}'
        db.add(row)
        db.commit()

    client = TestClient(app_module.app)
    body = client.get("/api/audit/verify").json()
    assert body["verified"] is False
    assert body["broken_at_seq"] == 0
