"""FreshCartAdapter: our own merchant, the only one Razorpay can honestly pay.

No live server here — a mock transport stands in for demo_store's HTTP API,
using the exact JSON shapes demo_store/app.py actually returns (verified live
against the running store while building this: real milk, real ₹66, a real
cart read back independently).
"""

import httpx
import pytest

from orderguard.commerce.base import AdapterError, StoreUnavailable
from orderguard.commerce.freshcart import FreshCartAdapter


def _catalog_response(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/catalog":
        return httpx.Response(200, json={
            "merchant": "freshcart_demo", "currency": "INR",
            "items": [
                {"sku": "milk_1l", "title": "Amul Taaza Milk 1L", "price_paise": 6600,
                 "in_stock": 40, "category": "dairy", "unit": "L", "attributes": {}},
                {"sku": "banana", "title": "Banana (dozen)", "price_paise": 1200,
                 "in_stock": 0, "category": "fruit", "unit": "dozen", "attributes": {}},
            ],
        })
    raise AssertionError(f"unexpected path {request.url.path}")


@pytest.fixture
def client():
    return httpx.AsyncClient(base_url="http://freshcart.test", transport=httpx.MockTransport(_catalog_response))


# --- search -------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_matching_products(client):
    async with FreshCartAdapter(client=client) as fc:
        offers = await fc.search("milk")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.store == "freshcart"
    assert offer.title == "Amul Taaza Milk 1L"
    assert offer.price_minor == 6600
    assert offer.available is True


@pytest.mark.asyncio
async def test_an_out_of_stock_item_is_marked_unavailable_not_hidden(client):
    async with FreshCartAdapter(client=client) as fc:
        offers = await fc.search("banana")

    assert len(offers) == 1
    assert offers[0].available is False


@pytest.mark.asyncio
async def test_no_match_is_an_empty_list_not_an_error(client):
    async with FreshCartAdapter(client=client) as fc:
        offers = await fc.search("xylophone")
    assert offers == []


# --- cart write and read-back -------------------------------------------

@pytest.mark.asyncio
async def test_add_to_cart_then_read_cart_agree():
    """The property that matters everywhere in this project: what was written
    and what is read back must be checkable against each other."""
    cart_state: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/add"):
            body = __import__("json").loads(request.content)
            cart_state[body["sku"]] = cart_state.get(body["sku"], 0) + body["quantity"]
        lines = [
            {"sku": "milk_1l", "title": "Amul Taaza Milk 1L", "quantity": qty,
             "unit_price_paise": 6600, "line_total_paise": qty * 6600}
            for sku, qty in cart_state.items()
        ]
        subtotal = sum(l["line_total_paise"] for l in lines)
        return httpx.Response(200, json={
            "cart_id": "test-cart", "merchant": "freshcart_demo", "currency": "INR",
            "lines": lines, "subtotal_paise": subtotal,
            "delivery_paise": 0 if subtotal == 0 else 3000,
            "total_paise": subtotal + (0 if subtotal == 0 else 3000),
        })

    client = httpx.AsyncClient(base_url="http://freshcart.test", transport=httpx.MockTransport(handler))
    async with FreshCartAdapter(client=client) as fc:
        written = await fc.add_to_cart("milk_1l", 2, cart_id="test-cart")
        observed = await fc.read_cart("test-cart")

    assert written.total_paise == observed.total_paise
    assert observed.lines[0].quantity == 2
    assert observed.lines[0].line_total_paise == 13200
    assert observed.total_paise == 16200          # + 3000 delivery, same as live


# --- failing closed -------------------------------------------------------

@pytest.mark.asyncio
async def test_a_dead_store_raises_store_unavailable_not_a_silent_empty_result():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    client = httpx.AsyncClient(base_url="http://freshcart.test", transport=httpx.MockTransport(boom))
    async with FreshCartAdapter(client=client) as fc:
        with pytest.raises(StoreUnavailable):
            await fc.search("milk")


@pytest.mark.asyncio
async def test_a_500_from_the_store_is_reported_not_swallowed():
    client = httpx.AsyncClient(
        base_url="http://freshcart.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    async with FreshCartAdapter(client=client) as fc:
        with pytest.raises(StoreUnavailable):
            await fc.search("milk")


@pytest.mark.asyncio
async def test_out_of_stock_purchase_attempt_is_refused_not_silently_shrunk():
    """demo_store itself refuses to oversell; the adapter must surface that as
    an error, never quietly add fewer than asked and call it a success."""
    client = httpx.AsyncClient(
        base_url="http://freshcart.test",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(409, json={"detail": "only 0 of banana in stock, asked for 6"})
        ),
    )
    async with FreshCartAdapter(client=client) as fc:
        with pytest.raises(AdapterError, match="banana"):
            await fc.add_to_cart("banana", 6, cart_id="test-cart")
