"""Real Swiggy OAuth 2.1 + PKCE + RFC 7591 dynamic client registration,
against the **Developer** (self-serve, `http://localhost`, no approval
needed) flow — confirmed directly from Swiggy's own docs before writing
this (2026-08-31): "You don't need approval to start — steps 1 through 5
below run on `http://localhost`, free."
(mcp.swiggy.com/builders/docs/start/developer/). Endpoints verified
directly against mcp.swiggy.com/builders/docs/start/authenticate/, not
guessed:

    POST https://mcp.swiggy.com/auth/register    (RFC 7591 dynamic registration)
    GET  https://mcp.swiggy.com/auth/authorize   (redirect, PKCE)
    POST https://mcp.swiggy.com/auth/token       (code exchange)

The one thing NOT confirmed by Swiggy's docs verbatim is ``/auth/register``'s
exact request/response field names beyond "RFC 7591" — this uses the
standard RFC 7591 minimal shape and reads ``client_id`` defensively from
whatever comes back, raising a clear ``SwiggyOAuthError`` rather than
guessing if it's absent. If Swiggy's actual response differs, this is the
one function to correct, not a reason this wasn't attempted.

Access tokens are 5 days (432000s, confirmed in the docs) with **no refresh
token in v1** — re-running this whole flow is the only way to renew one.
"""

from __future__ import annotations

import asyncio

import httpx

from .connector_accounts import generate_pkce_pair

__all__ = [
    "SwiggyOAuthError", "PendingAuthorization",
    "register_client", "build_authorize_url", "exchange_code",
]

_BASE = "https://mcp.swiggy.com"
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# Real, reproduced (2026-09-06): a Reconnect click failed with the raw
# resolver errno "[Errno 8] nodename nor servname provided, or not known",
# while three DNS lookups for the SAME host moments later all resolved
# fine and the backend had just completed a real cart write against it. A
# transient local resolver hiccup is not a reason to make someone re-run an
# OAuth flow, and an errno is not something they can act on.
_CONNECT_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0

# What the OS resolver actually says for "that name did not resolve", on
# macOS and Linux. Matched narrowly, so a genuine refusal (a real HTTP
# status, a TLS failure) is never softened into this friendlier message.
_DNS_FAILURE_MARKERS = (
    "nodename nor servname",              # macOS getaddrinfo
    "name or service not known",          # glibc
    "temporary failure in name resolution",
    "getaddrinfo failed",
)


def _connection_error_message(what: str, exc: Exception) -> str:
    """An errno is not a thing a user can act on. Say what failed in words,
    and whether trying again is actually worth their time."""
    if any(marker in str(exc).lower() for marker in _DNS_FAILURE_MARKERS):
        return (
            f"{what}: could not resolve mcp.swiggy.com. That is a local DNS "
            "or network problem, not Swiggy refusing the request — check "
            "your connection and try again."
        )
    return f"{what}: could not reach mcp.swiggy.com ({exc})"


async def _post_with_retry(
    http: httpx.AsyncClient, url: str, *, json: dict, what: str,
) -> httpx.Response:
    """POST, retrying ONLY connection-level failures — never a real HTTP
    response, however bad its status code, because that is Swiggy having
    genuinely answered and is not made truer by asking again."""
    last: Exception | None = None
    for attempt in range(_CONNECT_ATTEMPTS):
        try:
            return await http.post(url, json=json)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last = exc
            if attempt + 1 < _CONNECT_ATTEMPTS:
                await asyncio.sleep(_RETRY_DELAY_SECONDS)
        except httpx.HTTPError as exc:
            raise SwiggyOAuthError(f"{what}: {exc}") from exc
    assert last is not None
    raise SwiggyOAuthError(_connection_error_message(what, last)) from last


class SwiggyOAuthError(RuntimeError):
    """A real, reportable failure from Swiggy's own OAuth endpoints —
    never silently treated as success."""


class PendingAuthorization:
    """Held server-side between /connect and /callback — one per attempt.
    Not persisted: if the process restarts mid-flow the user just starts
    over, which is an acceptable local-single-user tradeoff.
    """

    __slots__ = ("connector_id", "redirect_uri", "code_verifier", "state", "client_id")

    def __init__(self, connector_id: str, redirect_uri: str, code_verifier: str, state: str, client_id: str):
        self.connector_id = connector_id
        self.redirect_uri = redirect_uri
        self.code_verifier = code_verifier
        self.state = state
        self.client_id = client_id


async def register_client(
    redirect_uri: str, *, client_name: str = "OrderGuard (local dev)",
    client: httpx.AsyncClient | None = None,
) -> str:
    """RFC 7591 dynamic client registration. Returns ``client_id``. Raises
    ``SwiggyOAuthError`` with the real response body on any failure —
    including the case this module's docstring names: Swiggy accepting the
    request but returning a response this code doesn't recognize. ``client``
    is injectable for tests (``httpx.MockTransport``).
    """
    body = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",  # public client, PKCE-secured
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        resp = await _post_with_retry(
            http, f"{_BASE}/auth/register", json=body,
            what="dynamic client registration failed",
        )
    finally:
        if owns_client:
            await http.aclose()

    if resp.status_code not in (200, 201):
        raise SwiggyOAuthError(
            f"dynamic client registration returned HTTP {resp.status_code}: {resp.text[:500]}"
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise SwiggyOAuthError(f"registration reply was not JSON: {resp.text[:500]}") from exc

    client_id = data.get("client_id")
    if not client_id:
        raise SwiggyOAuthError(f"registration reply had no client_id: {data!r}")
    return str(client_id)


def build_authorize_url(*, client_id: str, redirect_uri: str, code_challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp:tools",
    }
    query = httpx.QueryParams(params)
    return f"{_BASE}/auth/authorize?{query}"


async def exchange_code(
    *, code: str, code_verifier: str, redirect_uri: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, int | None, str]:
    """Returns ``(access_token, expires_in_seconds, scope)``."""
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        resp = await _post_with_retry(
            http, f"{_BASE}/auth/token", json=body,
            what="token exchange failed",
        )
    finally:
        if owns_client:
            await http.aclose()

    if resp.status_code != 200:
        raise SwiggyOAuthError(f"token exchange returned HTTP {resp.status_code}: {resp.text[:500]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SwiggyOAuthError(f"token reply was not JSON: {resp.text[:500]}") from exc

    access_token = data.get("access_token")
    if not access_token:
        raise SwiggyOAuthError(f"token reply had no access_token: {data!r}")
    return str(access_token), data.get("expires_in"), str(data.get("scope", ""))
