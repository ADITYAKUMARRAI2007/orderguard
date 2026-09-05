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


# --- transient DNS: retried, and reported in words, not an errno ----------

@pytest.mark.asyncio
async def test_a_transient_dns_failure_is_retried_and_then_succeeds(monkeypatch):
    """Real, reproduced (2026-09-06): a Reconnect click failed with the raw
    "[Errno 8] nodename nor servname provided, or not known", while three
    DNS lookups for the SAME host moments later all resolved fine. A
    transient resolver hiccup must not cost a user their OAuth flow."""
    from unittest.mock import AsyncMock, patch

    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError(
                "[Errno 8] nodename nor servname provided, or not known", request=request,
            )
        return httpx.Response(201, json={"client_id": "recovered"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("orderguard.agent.swiggy_oauth.asyncio.sleep", AsyncMock(return_value=None)):
        client_id = await register_client("http://localhost:8000/cb", client=client)
    await client.aclose()

    assert client_id == "recovered"
    assert attempts["n"] == 2  # failed once, retried, succeeded


@pytest.mark.asyncio
async def test_a_persistent_dns_failure_is_reported_in_plain_words():
    """An errno is not something a user can act on -- the message must name
    what failed and whether retrying is worth their time."""
    from unittest.mock import AsyncMock, patch

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "[Errno 8] nodename nor servname provided, or not known", request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("orderguard.agent.swiggy_oauth.asyncio.sleep", AsyncMock(return_value=None)):
        with pytest.raises(SwiggyOAuthError, match="could not resolve mcp.swiggy.com"):
            await register_client("http://localhost:8000/cb", client=client)
    await client.aclose()


@pytest.mark.asyncio
async def test_a_real_http_refusal_is_never_retried():
    """Swiggy genuinely answering, however badly, is not made truer by
    asking again -- only connection-level failures are retried."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SwiggyOAuthError, match="HTTP 403"):
        await register_client("http://localhost:8000/cb", client=client)
    await client.aclose()

    assert attempts["n"] == 1
