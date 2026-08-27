"""Offline contract tests for Shopify MCP responses.

Recorded-shape fixtures make the real-store adapter testable without touching a
merchant cart. The live probe is manual only; these tests are the regression
suite.
"""

import asyncio
import json

import httpx
import pytest

from orderguard.commerce import AdapterError, ShopifyMCPAdapter
from orderguard.commerce.shopify_mcp import minor_from_cart, minor_from_search


def _mcp(body: dict, *, is_error: bool = False) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"isError": is_error, "content": [{"type": "text", "text": json.dumps(body)}]},
    }


def test_search_prices_are_already_in_minor_units():
    assert minor_from_search({"amount": 26400, "currency": "INR"}) == (26400, "INR")


@pytest.mark.parametrize(
    "money",
    [
        {"amount": True, "currency": "INR"},
        {"amount": -1, "currency": "INR"},
        {"amount": "26400", "currency": "INR"},
        {"amount": 100, "currency": "not-a-currency"},
    ],
)
def test_search_rejects_schema_drift(money):
    with pytest.raises(AdapterError):
        minor_from_search(money)


def test_cart_prices_are_decimal_major_unit_strings():
    assert minor_from_cart({"amount": "528.10", "currency": "INR"}) == (52810, "INR")


@pytest.mark.parametrize(
    "money",
    [
        {"amount": 52810, "currency": "INR"},
        {"amount": "NaN", "currency": "INR"},
        {"amount": "1.001", "currency": "INR"},
        {"amount": "-0.01", "currency": "INR"},
    ],
)
def test_cart_rejects_unsafe_money_shapes(money):
    with pytest.raises(AdapterError):
        minor_from_cart(money)


def test_search_exposes_each_variant_not_just_the_first():
    body = {
        "products": [
            {
                "id": "p1",
                "title": "Millet cereal",
                "variants": [
                    {"id": "v-small", "title": "100 g", "price": {"amount": 10000, "currency": "INR"}, "availability": {"available": True}},
                    {"id": "v-large", "title": "1 kg", "price": {"amount": 70000, "currency": "INR"}, "availability": {"available": True}},
                ],
            }
        ]
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mcp(body))

    async def run() -> list:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ShopifyMCPAdapter("shop.example", client=client).search("millet")

    offers = asyncio.run(run())

    assert [(offer.variant_id, offer.price_minor) for offer in offers] == [
        ("v-small", 10000),
        ("v-large", 70000),
    ]


def test_cart_uses_the_exact_line_total_without_inventing_unit_price():
    adapter = ShopifyMCPAdapter("shop.example")
    cart = adapter._read_cart_object(
        {
            "cart": {
                "id": "cart-1",
                "cost": {
                    "subtotal_amount": {"amount": "1.00", "currency": "INR"},
                    "total_amount": {"amount": "1.00", "currency": "INR"},
                },
                "lines": [
                    {
                        "id": "line-1",
                        "quantity": 3,
                        "cost": {"total_amount": {"amount": "1.00", "currency": "INR"}},
                        "merchandise": {"id": "variant-1", "product": {"title": "Example"}},
                    }
                ],
            }
        }
    )

    assert cart.total_paise == 100
    assert cart.lines[0].line_total_paise == 100
    assert cart.lines[0].unit_price_paise is None


def test_cart_rejects_currency_mismatch():
    adapter = ShopifyMCPAdapter("shop.example")
    with pytest.raises(AdapterError, match="currencies disagree"):
        adapter._read_cart_object(
            {
                "cart": {
                    "id": "cart-1",
                    "cost": {
                        "subtotal_amount": {"amount": "1.00", "currency": "INR"},
                        "total_amount": {"amount": "1.00", "currency": "USD"},
                    },
                    "lines": [],
                }
            }
        )


def test_mcp_tool_errors_are_not_treated_as_carts():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_mcp({"message": "nope"}, is_error=True))

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = ShopifyMCPAdapter("shop.example", client=client)
            with pytest.raises(AdapterError, match="tool rejected"):
                await adapter.search("milk")

    asyncio.run(run())
