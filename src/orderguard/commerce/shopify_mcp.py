"""Talks to a real Shopify store over the Storefront MCP endpoint.

Selected Shopify stores expose ``https://<domain>/api/mcp``. The registry is an
allowlist of stores verified by a manual probe; the adapter does not claim that
every Shopify merchant exposes the endpoint. No key is used for those probes.

Two things about this endpoint decide how the code below is written.

**1. It returns money in two different shapes.**

``search_catalog``  ->  ``{"amount": 26400,   "currency": "INR"}``   integer, paise
``get_cart``        ->  ``{"amount": "528.0", "currency": "INR"}``   string,  rupees

Same API, same currency, one call apart. Feeding either into the same parser
gives you a hundredfold error in one direction or the other, and reaching for
``float()`` on the second one puts binary floating point into a payment path.
So there are two named parsers here and no general-purpose one. The type tells
you the unit: ``int`` means minor units already, ``str`` means major units to be
converted through ``Decimal``. See D-022.

**2. Its replies contain text addressed to the AI.**

Both ``update_cart`` and ``get_cart`` return an ``instructions`` field — prose
telling the assistant to ask about discount codes and walk the buyer to
checkout. Shopify wrote it and it is benign. That is not the point. It is a
merchant-controlled string arriving on the same wire as the cart totals, and
millions of stores can put whatever they like there.

We drop it. It is never parsed, never stored, never shown to a model. Product
identifiers, quantities, prices and availability are still merchant-supplied
facts, but are validated and compared deterministically. Merchant prose never
gets authority over a gate. See D-023.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

import httpx

from .base import AdapterError, CartLine, Location, ObservedCart, Offer, StoreUnavailable

__all__ = ["ShopifyMCPAdapter", "minor_from_search", "minor_from_cart"]

_TIMEOUT = httpx.Timeout(20.0, connect=8.0)
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")


# --------------------------------------------------------------------------
# money — two shapes, two parsers, never one clever one
# --------------------------------------------------------------------------

def minor_from_search(money: dict) -> tuple[int, str]:
    """Parse ``{"amount": 26400, "currency": "INR"}`` — already minor units."""
    amount = money.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise AdapterError(
            f"search price should be an integer of minor units, got {amount!r}"
        )
    if amount < 0:
        raise AdapterError(f"negative price: {amount!r}")
    return amount, _currency(money)


def minor_from_cart(money: dict) -> tuple[int, str]:
    """Parse ``{"amount": "528.0", "currency": "INR"}`` — major units, as text.

    Goes through ``Decimal``. ``float("528.0") * 100`` is 52799.99999999999 for
    the wrong inputs, and a payment system may not round its way out of that.
    """
    amount = money.get("amount")
    if not isinstance(amount, str):
        raise AdapterError(f"cart price has unusable type: {amount!r}")
    try:
        d = Decimal(str(amount))
    except InvalidOperation as exc:
        raise AdapterError(f"cart price is not a number: {amount!r}") from exc
    if not d.is_finite() or d < 0:
        raise AdapterError(f"negative cart price: {amount!r}")
    if -d.as_tuple().exponent > 2:
        raise AdapterError(f"cart price has sub-paise precision: {amount!r}")
    return int(d.scaleb(2)), _currency(money)


def _currency(money: dict) -> str:
    currency = str(money.get("currency") or "").upper()
    if not _CURRENCY.fullmatch(currency):
        raise AdapterError(f"invalid currency: {currency!r}")
    return currency


# --------------------------------------------------------------------------

class ShopifyMCPAdapter:
    """One instance per store."""

    def __init__(
        self,
        store: str,
        store_label: str = "",
        client: httpx.AsyncClient | None = None,
        country: str = "IN",
    ) -> None:
        self.store = store
        self.store_label = store_label or store.split(".")[0].title()
        self.url = f"https://{store}/api/mcp"
        self.country = country
        self._client = client
        self._owned = client is None
        self._rpc_id = 0

    async def __aenter__(self) -> "ShopifyMCPAdapter":
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- transport ---------------------------------------------------------

    async def _call(self, tool: str, arguments: dict) -> dict:
        if self._client is None:
            raise AdapterError("adapter used outside its context manager")

        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._rpc_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }

        try:
            resp = await self._client.post(self.url, json=payload, headers=_HEADERS)
        except httpx.HTTPError as exc:
            raise StoreUnavailable(f"{self.store}: {exc}") from exc

        if resp.status_code != 200:
            raise StoreUnavailable(f"{self.store}: HTTP {resp.status_code}")

        try:
            envelope = resp.json()
        except ValueError as exc:
            raise StoreUnavailable(f"{self.store}: reply was not JSON") from exc

        if not isinstance(envelope, dict):
            raise AdapterError(f"{self.store}/{tool}: reply was not an object")
        if "error" in envelope:
            raise AdapterError(f"{self.store}/{tool}: JSON-RPC error")

        try:
            result = envelope["result"]
            if result.get("isError"):
                raise AdapterError(f"{self.store}/{tool}: tool rejected the request")
            text = next(block["text"] for block in result["content"] if "text" in block)
            body = json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AdapterError(f"{self.store}/{tool}: unreadable reply") from exc
        if not isinstance(body, dict):
            raise AdapterError(f"{self.store}/{tool}: reply was not an object")
        return body

    # --- the four operations ----------------------------------------------

    async def search(
        self, query: str, limit: int = 10, location: Location | None = None
    ) -> list[Offer]:
        """Search a store's catalogue.

        ``location`` is passed through as Shopify's buyer context. Note what is
        NOT passed: ``filters.price``. The endpoint accepts a price ceiling and
        does not honour it — asking for products under Rs 300 returned two above
        Rs 300 (F-013). Budget filtering is done in our own code, where it can be
        trusted, and this call must not imply otherwise.
        """
        context = (location or Location(country=self.country)).as_context()
        body = await self._call(
            "search_catalog", {"catalog": {"query": query, "context": context}}
        )

        offers: list[Offer] = []
        for product in body.get("products") or []:
            variants = product.get("variants") or []
            for variant in variants:
                price = variant.get("price") or {}
                try:
                    price_minor, currency = minor_from_search(price)
                    offer = Offer(
                        store=self.store,
                        store_label=self.store_label,
                        product_id=str(product.get("id") or ""),
                        variant_id=str(variant.get("id") or ""),
                        title=str(product.get("title") or "").strip(),
                        variant_title=_variant_title(variant),
                        price_minor=price_minor,
                        currency=currency,
                        available=bool(
                            (variant.get("availability") or {}).get("available", False)
                        ),
                        url=str(product.get("url") or ""),
                        image=_first_image(product, variant),
                    )
                except (AdapterError, ValueError):
                    continue                  # malformed offer: skip rather than guess
                offers.append(offer)
                if len(offers) >= limit:
                    return offers
        return offers

    async def add_to_cart(
        self, variant_id: str, quantity: int, cart_id: str | None = None
    ) -> ObservedCart:
        if quantity < 1:
            raise AdapterError(f"quantity must be at least 1, got {quantity}")

        args: dict = {
            "add_items": [{"product_variant_id": variant_id, "quantity": quantity}]
        }
        if cart_id:
            args["cart_id"] = cart_id

        body = await self._call("update_cart", args)
        if body.get("errors"):
            raise AdapterError(f"{self.store}: cart update rejected")
        return self._read_cart_object(body)

    async def read_cart(self, cart_id: str) -> ObservedCart:
        """Ask the store what is actually in the cart.

        Deliberately a separate round trip from ``add_to_cart``. Reusing the
        reply from the write would only tell us what we asked for; this tells
        us what the store did.
        """
        return self._read_cart_object(await self._call("get_cart", {"cart_id": cart_id}))

    # --- parsing -----------------------------------------------------------

    def _read_cart_object(self, body: dict) -> ObservedCart:
        # `body["instructions"]` is dropped here, on purpose. See the module
        # docstring: it is merchant-controlled prose aimed at the model.
        cart = body.get("cart") or {}
        if not isinstance(cart, dict) or not cart.get("id"):
            raise AdapterError(f"{self.store}: cart reply has no cart id")

        cost = cart.get("cost") or {}
        total_minor, currency = minor_from_cart(cost.get("total_amount") or {})
        subtotal_minor, subtotal_currency = minor_from_cart(cost.get("subtotal_amount") or {})
        if subtotal_currency != currency:
            raise AdapterError(f"{self.store}: cart currencies disagree")

        lines: list[CartLine] = []
        for raw in cart.get("lines") or []:
            quantity = raw.get("quantity")
            if not isinstance(quantity, int) or isinstance(quantity, bool):
                raise AdapterError(f"{self.store}: cart line quantity is {quantity!r}")

            cost = raw.get("cost") or {}
            line_total, line_currency = minor_from_cart(cost.get("total_amount") or {})
            if line_currency != currency:
                raise AdapterError(f"{self.store}: cart-line currency disagrees")

            merchandise = raw.get("merchandise") or {}
            product = merchandise.get("product") or {}

            lines.append(
                CartLine(
                    line_id=str(raw.get("id") or ""),
                    variant_id=str(merchandise.get("id") or ""),
                    sku=str(merchandise.get("sku") or merchandise.get("id") or ""),
                    title=str(product.get("title") or merchandise.get("title") or ""),
                    quantity=quantity,
                    line_total_paise=line_total,
                )
            )

        return ObservedCart(
            merchant=self.store,
            cart_id=str(cart.get("id") or ""),
            currency=currency,
            lines=lines,
            subtotal_paise=subtotal_minor,
            total_paise=total_minor,
            checkout_url=str(cart.get("checkout_url") or ""),
        )


# --- small helpers ---------------------------------------------------------

def _variant_title(variant: dict) -> str:
    title = str(variant.get("title") or "")
    return "" if title == "Default Title" else title


def _first_image(product: dict, variant: dict) -> str:
    for source in (variant, product):
        for media in source.get("media") or []:
            if media.get("type") == "image" and media.get("url"):
                return str(media["url"])
    return ""
