"""Swiggy OAuth 2.1 + PKCE + RFC 7591, against the endpoints verified
directly from mcp.swiggy.com's own docs (see swiggy_oauth.py's docstring).
Offline: every HTTP call goes through ``httpx.MockTransport``, matching this
repo's existing pattern in ``test_shopify_mcp.py``.
"""

import httpx
import pytest

from orderguard.agent.swiggy_oauth import (
    SwiggyOAuthError, build_authorize_url, exchange_code, register_client,
)


@pytest.mark.asyncio
async def test_register_client_returns_the_client_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/register"
        return httpx.Response(201, json={"client_id": "abc123"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client_id = await register_client("http://localhost:8000/api/connectors/swiggy/callback", client=client)
    await client.aclose()
    assert client_id == "abc123"


@pytest.mark.asyncio
async def test_register_client_raises_on_a_missing_client_id():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"unexpected": "shape"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SwiggyOAuthError):
        await register_client("http://localhost:8000/callback", client=client)
    await client.aclose()


@pytest.mark.asyncio
async def test_register_client_raises_on_non_2xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad redirect_uri")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SwiggyOAuthError):
        await register_client("http://localhost:8000/callback", client=client)
    await client.aclose()


def test_build_authorize_url_has_all_required_params():
    url = build_authorize_url(
        client_id="abc123", redirect_uri="http://localhost:8000/cb",
        code_challenge="challenge-value", state="csrf-token",
    )
    assert url.startswith("https://mcp.swiggy.com/auth/authorize?")
    assert "response_type=code" in url
    assert "client_id=abc123" in url
    assert "code_challenge_method=S256" in url
    assert "state=csrf-token" in url


@pytest.mark.asyncio
async def test_exchange_code_returns_token_and_expiry():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/token"
        return httpx.Response(200, json={
            "access_token": "eyJ...", "token_type": "Bearer",
            "expires_in": 432000, "scope": "mcp:tools",
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    token, expires_in, scope = await exchange_code(
        code="real-code", code_verifier="verifier", redirect_uri="http://localhost:8000/cb", client=client,
    )
    await client.aclose()
    assert token == "eyJ..."
    assert expires_in == 432000
    assert scope == "mcp:tools"


@pytest.mark.asyncio
async def test_exchange_code_raises_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SwiggyOAuthError):
        await exchange_code(code="bad", code_verifier="v", redirect_uri="http://localhost:8000/cb", client=client)
    await client.aclose()
