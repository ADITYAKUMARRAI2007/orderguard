"""Tests for the demo shop.

Two kinds here:

* it works — catalog loads, cart adds up, stock is respected
* **it cannot be tricked** — prices come from the server, hostile text is escaped

The second kind is the point.
"""

import pytest
from fastapi.testclient import TestClient

from demo_store.app import app
from demo_store.catalog import CATALOG, HOSTILE_SKU, get_product


@pytest.fixture
def client():
    c = TestClient(app)
    c.delete("/api/cart/test-cart")      # start clean
    yield c
    c.delete("/api/cart/test-cart")


# --- it works ---------------------------------------------------------------

def test_shop_is_alive(client):
    assert client.get("/api/health").json()["ok"] is True


def test_catalog_lists_products(client):
    body = client.get("/api/catalog").json()
    assert body["merchant"] == "freshcart_demo"
    assert body["currency"] == "INR"
    assert len(body["items"]) == len(CATALOG)


def test_every_price_is_a_whole_number_of_paise(client):
    """No floats anywhere in the shop's prices."""
    for item in client.get("/api/catalog").json()["items"]:
        assert isinstance(item["price_paise"], int)


def test_adding_to_cart_adds_up(client):
    client.post("/api/cart/test-cart/add", json={"sku": "milk_1l", "quantity": 2})
    cart = client.post(
        "/api/cart/test-cart/add", json={"sku": "banana", "quantity": 6}
    ).json()

    # milk 2 x 6600 = 13200, bananas 6 x 1200 = 7200, delivery 3000
    assert cart["subtotal_paise"] == 20400
    assert cart["total_paise"] == 23400


def test_empty_cart_has_no_delivery_charge(client):
    cart = client.get("/api/cart/test-cart").json()
    assert cart["total_paise"] == 0
    assert cart["delivery_paise"] == 0


def test_unknown_product_is_refused(client):
    r = client.post("/api/cart/test-cart/add", json={"sku": "unicorn", "quantity": 1})
    assert r.status_code == 404


def test_cannot_buy_more_than_the_shop_has(client):
    """Butter is deliberately out of stock."""
    r = client.post("/api/cart/test-cart/add", json={"sku": "butter_100g", "quantity": 1})
    assert r.status_code == 409
    assert "in stock" in r.json()["detail"]


def test_stock_limit_counts_across_repeated_adds(client):
    """Five adds of 2 must not sneak past a stock of 5."""
    p = get_product("coffee_premium")
    assert p.in_stock == 5

    for _ in range(2):
        client.post("/api/cart/test-cart/add", json={"sku": p.sku, "quantity": 2})
    r = client.post("/api/cart/test-cart/add", json={"sku": p.sku, "quantity": 2})
    assert r.status_code == 409          # 4 already in, 2 more would be 6


# --- it cannot be tricked ---------------------------------------------------

def test_browser_cannot_send_a_price(client):
    """The most important test in this file.

    If the browser could name a price, anyone with devtools could buy a
    ₹550 coffee for ₹1. The request shape simply has no price field.
    """
    r = client.post(
        "/api/cart/test-cart/add",
        json={"sku": "coffee_premium", "quantity": 1, "price_paise": 1},
    )
    assert r.status_code == 422          # rejected outright, not ignored


def test_price_always_comes_from_the_catalog(client):
    cart = client.post(
        "/api/cart/test-cart/add", json={"sku": "coffee_premium", "quantity": 1}
    ).json()
    assert cart["lines"][0]["unit_price_paise"] == CATALOG["coffee_premium"].price_paise


def test_negative_and_zero_quantities_are_refused(client):
    for bad in (0, -5):
        r = client.post("/api/cart/test-cart/add", json={"sku": "banana", "quantity": bad})
        assert r.status_code == 422


def test_absurd_quantity_is_refused(client):
    r = client.post("/api/cart/test-cart/add", json={"sku": "banana", "quantity": 10_000})
    assert r.status_code == 422


def test_the_hostile_product_exists_in_the_real_catalog(client):
    """The injection attempt is live in the shop, not hidden in a test file.

    Every demo run therefore exercises the defence.
    """
    hostile = get_product(HOSTILE_SKU)
    assert "SYSTEM:" in hostile.title
    assert "99999" in hostile.title


def test_hostile_product_can_still_be_bought_normally(client):
    """It is a normal product with a nasty name. It must behave normally.

    The text is data. It does nothing.
    """
    cart = client.post(
        "/api/cart/test-cart/add", json={"sku": HOSTILE_SKU, "quantity": 1}
    ).json()
    assert cart["lines"][0]["unit_price_paise"] == 32000
    assert cart["subtotal_paise"] == 32000      # not ₹99999, not free
