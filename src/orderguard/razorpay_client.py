"""The only code in this project allowed to talk to Razorpay.

Two calls, both read-or-create, never a client-trusted "it worked":

    create_order    POST /v1/orders     — headless, no browser needed
    fetch_payment   GET  /v1/payments/:id — the independent read that
                    ``verify_payment`` (payment.py) compares against

Authorization step (the user completing Checkout, entering UPI/card details) is
deliberately outside this module. It happens in the browser, on Razorpay's own
page. This module never sees a card number, a UPI PIN, or an OTP.

httpx only, per D-017 — F-005 found ``urllib.request`` blocked at Groq's edge
where ``httpx`` was not; the project standardised rather than special-case one
provider.
"""

from __future__ import annotations

import os

import httpx

__all__ = ["RazorpayClient", "RazorpayError", "client_from_env"]

_BASE = "https://api.razorpay.com/v1"
_TIMEOUT = httpx.Timeout(20.0, connect=8.0)


class RazorpayError(RuntimeError):
    """Razorpay refused the request or the response could not be read."""


class RazorpayClient:
    """Thin wrapper. Test-mode keys only — this project moves no real money."""

    def __init__(self, key_id: str, key_secret: str, client: httpx.AsyncClient | None = None):
        if not key_id.startswith("rzp_test_"):
            raise RazorpayError(
                f"refusing to use a non-test-mode key ({key_id[:8]}...). "
                "This project never touches live credentials."
            )
        self.key_id = key_id
        self._secret = key_secret
        self._client = client
        self._owned = client is None

    async def __aenter__(self) -> "RazorpayClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=_BASE, auth=(self.key_id, self._secret), timeout=_TIMEOUT
            )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if self._client is None:
            raise RazorpayError("client used outside its context manager")
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RazorpayError(f"{method} {path}: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise RazorpayError(f"{method} {path}: reply was not JSON") from exc

        if response.status_code >= 400:
            reason = (body.get("error") or {}).get("description", str(body))
            raise RazorpayError(f"{method} {path} -> HTTP {response.status_code}: {reason}")
        return body

    async def create_order(
        self, *, amount_paise: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict:
        """Headless. No browser needed for this half of the payment path."""
        if amount_paise < 100:
            raise RazorpayError(f"amount must be at least 100 paise, got {amount_paise}")
        return await self._request(
            "POST", "/orders",
            json={
                "amount": amount_paise, "currency": currency,
                "receipt": receipt[:40], "notes": notes,
            },
        )

    async def find_order_by_receipt(self, receipt: str) -> dict | None:
        """Resolves a create_order call whose response was lost — timeout,
        dropped connection, anything short of a clean success or a clean 4xx.
        ``receipt`` is what create_order already sets to our idempotency key,
        so this needs no new plumbing to correlate against. Returns ``None``
        when Razorpay genuinely has no record of it, which is itself the
        answer: safe to retry, nothing was created.

        Real, reproduced gap (see G_SINGLE_CANDIDATE): a receipt is our own
        idempotency key and should be unique, but this is Razorpay's own
        index, not ours — trusting ``items[0]`` without checking for a second
        match would silently pick one of two orders if Razorpay ever
        returned more than one. Refuses instead of guessing, matching every
        other "never guess" boundary in this module.
        """
        body = await self._request("GET", "/orders", params={"receipt": receipt[:40]})
        items = body.get("items") or []
        if len(items) > 1:
            raise RazorpayError(
                f"receipt {receipt!r} matched {len(items)} orders — refusing to guess which one is real"
            )
        return items[0] if items else None

    async def fetch_payment(self, payment_id: str) -> dict:
        """The independent read. Never skip this for what a browser reported."""
        return await self._request("GET", f"/payments/{payment_id}")


def client_from_env() -> tuple[str, str]:
    """Read (key_id, key_secret) from the environment. Never hardcoded."""
    key_id = os.environ.get("RZP_KEY_ID", "").strip()
    key_secret = os.environ.get("RZP_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise RazorpayError("RZP_KEY_ID and RZP_KEY_SECRET must be set in .env")
    return key_id, key_secret
