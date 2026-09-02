"""RazorpayClient's own behaviour, mocked at the transport level — no network.

find_order_by_receipt is the resolution path for a create_order call whose
response was lost: this is what proves it actually parses Razorpay's real
response shape (an ``items`` list) correctly, in both directions.
"""

import asyncio

import httpx
import pytest

from orderguard.razorpay_client import RazorpayClient, RazorpayError

KEY_ID = "rzp_test_fake"
KEY_SECRET = "fake_secret"


def _client(handler: httpx.MockTransport | callable) -> RazorpayClient:
    transport = handler if isinstance(handler, httpx.MockTransport) else httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://api.razorpay.com/v1", transport=transport,
    )
    return RazorpayClient(KEY_ID, KEY_SECRET, client=http_client)


def test_a_non_test_mode_key_is_refused_immediately():
    with pytest.raises(RazorpayError, match="non-test-mode"):
        RazorpayClient("rzp_live_shouldnotbeused", "secret")


def test_find_order_by_receipt_returns_the_first_match():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["receipt"] == "og_key_123"
        return httpx.Response(200, json={
            "entity": "collection", "count": 1,
            "items": [{"id": "order_found", "receipt": "og_key_123", "amount": 29900}],
        })

    async def run():
        client = _client(handler)
        async with client:
            return await client.find_order_by_receipt("og_key_123")

    order = asyncio.run(run())
    assert order["id"] == "order_found"


def test_find_order_by_receipt_returns_none_when_razorpay_has_no_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})

    async def run():
        client = _client(handler)
        async with client:
            return await client.find_order_by_receipt("og_key_never_created")

    assert asyncio.run(run()) is None


def test_find_order_by_receipt_truncates_to_forty_characters_like_create_order():
    long_key = "og_" + "x" * 60
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["receipt"] = request.url.params["receipt"]
        return httpx.Response(200, json={"items": []})

    async def run():
        client = _client(handler)
        async with client:
            await client.find_order_by_receipt(long_key)

    asyncio.run(run())
    assert len(seen["receipt"]) == 40
    assert seen["receipt"] == long_key[:40]


def test_find_order_by_receipt_refuses_to_guess_between_multiple_matches():
    """Regression for a real, found gap (see G_SINGLE_CANDIDATE): a receipt
    is our own idempotency key and should be unique, but this reads
    Razorpay's own index, not ours. The old code took items[0] with no check
    at all — if Razorpay ever returned two orders for one receipt, it would
    silently pick one instead of refusing."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "entity": "collection", "count": 2,
            "items": [
                {"id": "order_a", "receipt": "og_key_123", "amount": 29900},
                {"id": "order_b", "receipt": "og_key_123", "amount": 29900},
            ],
        })

    async def run():
        client = _client(handler)
        async with client:
            await client.find_order_by_receipt("og_key_123")

    with pytest.raises(RazorpayError, match="matched 2 orders"):
        asyncio.run(run())


def test_a_gateway_error_response_raises_razorpay_error_not_a_silent_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"description": "bad auth"}})

    async def run():
        client = _client(handler)
        async with client:
            await client.find_order_by_receipt("og_key_123")

    with pytest.raises(RazorpayError, match="bad auth"):
        asyncio.run(run())
