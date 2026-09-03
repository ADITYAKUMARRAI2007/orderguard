"""Talks to FreshCart — our own demo merchant, run by ``demo_store/app.py``.

Every other adapter in this package reaches a store we do not control and
therefore can never pay through Razorpay (D-020: Shopify collects its own
checkout, not us). FreshCart is the one exception on purpose. It is where the
Razorpay test payment actually runs, because that is the only merchant whose
money we are honestly in a position to move.

Same shape as ``ShopifyMCPAdapter`` — ``search``, ``add_to_cart``, ``read_cart``,
used as an async context manager — so the rest of the app does not need to
know which kind of store it is talking to. Money is already integer paise on
both sides here; unlike Shopify (D-022) there is no unit mismatch to guard
against, because we wrote both ends of this wire.
"""

from __future__ import annotations

import os

import httpx

from .base import AdapterError, CartLine, ObservedCart, Offer, StoreUnavailable

__all__ = ["FreshCartAdapter"]

# 45s / 30s connect, not the original 10s / 5s: on a free-tier host, FreshCart
# spins down after idle and a cold start alone measured 22.5s in production --
# comfortably over the old timeout on its own, before the request itself ran.
# The failure mode was not "the store is down"; it was "the store is starting
# up and we stopped waiting" (FAILURE_LOG.md F-034 follow-up).
_TIMEOUT = httpx.Timeout(45.0, connect=30.0)


class FreshCartAdapter:
    store = "freshcart"
    store_label = "FreshCart"

    def __init__(self, base_url: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = (base_url or os.environ.get("FRESHCART_URL") or "http://127.0.0.1:8002").rstrip("/")
        self._client = client
        self._owned = client is None

    async def __aenter__(self) -> "FreshCartAdapter":
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str) -> dict:
        if self._client is None:
            raise AdapterError("adapter used outside its context manager")
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"freshcart: {exc}") from exc
        return self._read(response)

    async def _post(self, path: str, json: dict) -> dict:
        if self._client is None:
            raise AdapterError("adapter used outside its context manager")
        try:
            response = await self._client.post(path, json=json)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"freshcart: {exc}") from exc
        return self._read(response)

    def _read(self, response: httpx.Response) -> dict:
        if response.status_code >= 500:
            raise StoreUnavailable(f"freshcart: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise AdapterError("freshcart: reply was not JSON") from exc
        if response.status_code >= 400:
            raise AdapterError(f"freshcart: {body.get('detail', response.status_code)}")
        return body

    async def search(self, query: str, limit: int = 10, location=None) -> list[Offer]:
        """FreshCart's catalogue is small enough to filter in-process. Word
        overlap on title/category — same rule as everywhere else in this
        project, never a fuzzy score dressed up as intelligence."""
        catalog = await self._get("/api/catalog")
        words = {w for w in query.lower().split() if len(w) > 2}

        offers: list[Offer] = []
        for item in catalog.get("items") or []:
            haystack = f"{item['title']} {item.get('category', '')}".lower()
            if words and not any(w in haystack for w in words):
                continue
            offers.append(Offer(
                store=self.store, store_label=self.store_label,
                product_id=item["sku"], variant_id=item["sku"], title=item["title"],
                price_minor=item["price_paise"], currency=catalog.get("currency", "INR"),
                available=item.get("in_stock", 0) > 0,
                url=f"{self.base_url}/", image="",
            ))
            if len(offers) >= limit:
                break
        return offers

    async def add_to_cart(self, variant_id: str, quantity: int, cart_id: str | None = None) -> ObservedCart:
        cart_id = cart_id or "orderguard-session"
        await self._post(f"/api/cart/{cart_id}/add", json={"sku": variant_id, "quantity": quantity})
        return await self.read_cart(cart_id)

    async def read_cart(self, cart_id: str) -> ObservedCart:
        body = await self._get(f"/api/cart/{cart_id}")
        lines = [
            CartLine(
                sku=line["sku"], variant_id=line["sku"], title=line["title"],
                quantity=line["quantity"], unit_price_paise=line["unit_price_paise"],
                line_total_paise=line["line_total_paise"],
            )
            for line in body.get("lines") or []
        ]
        return ObservedCart(
            merchant=self.store, cart_id=body.get("cart_id", cart_id),
            currency=body.get("currency", "INR"), lines=lines,
            subtotal_paise=body.get("subtotal_paise", 0),
            delivery_paise=body.get("delivery_paise", 0),
            total_paise=body.get("total_paise", 0),
        )
