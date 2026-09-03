"""Strict, connector-specific MCP result normalizers.

Each adapter accepts only a documented fixture shape. It never searches a
bag of vaguely similar field names and never silently drops malformed rows.
The MCP content envelope is decoded separately from connector semantics so
the actual tool payload remains available on ``ToolCallEvent.result``.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..commerce.base import Offer
from ..commerce.search import ScoredOffer
from ..commerce.shopify_mcp import minor_from_search
from .results import CommerceResult, ConnectorResult, DevTaskResult
from .runtime.base import ToolCallEvent

__all__ = [
    "ConnectorPayloadError", "NormalizationError", "ShopifyNormalizer",
    "SwiggyNormalizer", "GitHubNormalizer", "normalize",
]


class ConnectorPayloadError(RuntimeError):
    """A connector returned no result or a shape we have not proven."""

    def __init__(self, connector_id: str, tool_name: str, reason: str):
        self.connector_id = connector_id
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"{connector_id}/{tool_name}: {reason}")


NormalizationError = ConnectorPayloadError


class _Normalizer(Protocol):
    def normalize(
        self, call: ToolCallEvent, *, budget_minor: int | None = None
    ) -> CommerceResult | DevTaskResult | None: ...


_FIXTURE_MODEL = ConfigDict(extra="ignore", strict=True)


class _Money(BaseModel):
    model_config = _FIXTURE_MODEL
    amount: int
    currency: str = Field(min_length=3, max_length=3)


class _Availability(BaseModel):
    model_config = _FIXTURE_MODEL
    available: bool


class _ShopifyVariant(BaseModel):
    model_config = _FIXTURE_MODEL
    id: str
    title: str = ""
    price: _Money
    availability: _Availability


class _ShopifyProduct(BaseModel):
    model_config = _FIXTURE_MODEL
    id: str
    title: str = Field(min_length=1)
    url: str = ""
    variants: list[_ShopifyVariant]


class _ShopifySearch(BaseModel):
    model_config = _FIXTURE_MODEL
    products: list[_ShopifyProduct]


class ShopifyNormalizer:
    """Fixture: Shopify Storefront MCP ``search_catalog`` JSON body."""

    def normalize(self, call: ToolCallEvent, *, budget_minor: int | None = None) -> CommerceResult:
        if call.tool_name != "search_catalog":
            raise ConnectorPayloadError(call.connector_id, call.tool_name, "unsupported operation")
        data = _decoded_payload(call)
        try:
            body = _ShopifySearch.model_validate(data)
        except ValidationError as exc:
            # Operator-only diagnostic (F-017: user-facing message stays
            # generic). The exception's own message names only the first
            # mismatched field/location, not what shape actually arrived --
            # not enough to root-cause a live incident (see F-037/live
            # report of this exact error). Printing the real raw shape here
            # is what actually lets the next occurrence be root-caused
            # instead of re-guessed.
            print(
                f"[agent] ShopifyNormalizer raw call.result type={type(call.result).__name__} "
                f"repr={repr(call.result)[:2000]!r} decoded data type={type(data).__name__} "
                f"repr={repr(data)[:2000]!r}",
                file=sys.stderr,
            )
            raise _validation_error(call, exc) from None

        store = call.resource_ref or call.arguments.get("store")
        if not isinstance(store, str) or not store:
            raise ConnectorPayloadError(call.connector_id, call.tool_name, "missing verified store provenance")

        offers: list[ScoredOffer] = []
        for product in body.products:
            for variant in product.variants:
                try:
                    price_minor, currency = minor_from_search(variant.price.model_dump())
                    offer = Offer(
                        store=store,
                        store_label=store,
                        product_id=product.id,
                        variant_id=variant.id,
                        title=product.title.strip(),
                        variant_title=variant.title,
                        price_minor=price_minor,
                        currency=currency,
                        available=variant.availability.available,
                        url=product.url,
                    )
                except Exception as exc:
                    raise ConnectorPayloadError(
                        call.connector_id, call.tool_name,
                        f"invalid Shopify offer: {type(exc).__name__}: {exc}",
                    ) from None
                offers.append(_scored(offer, budget_minor=budget_minor))
        return CommerceResult(merchant="shopify", offers=offers)


class _SwiggyPrice(BaseModel):
    model_config = _FIXTURE_MODEL
    offerPrice: int = Field(ge=0)  # rupees, not paise — see normalize() below


class _SwiggyVariation(BaseModel):
    model_config = _FIXTURE_MODEL
    # spinId, not skuId, is what update_cart's real schema requires
    # (`items[].spinId`, required; `skuId` is accepted but optional) —
    # confirmed directly against the live tool schema, not assumed. An
    # earlier version of this normalizer used skuId as variant_id, which
    # would have made "add this exact variant to the cart" impossible.
    spinId: str = Field(min_length=1)
    skuId: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    price: _SwiggyPrice
    isInStockAndAvailable: bool
    quantityDescription: str = ""
    imageUrl: str = ""


class _SwiggyProduct(BaseModel):
    model_config = _FIXTURE_MODEL
    productId: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    inStock: bool
    isAvail: bool
    variations: list[_SwiggyVariation] = Field(min_length=1)


class _SwiggySearch(BaseModel):
    model_config = _FIXTURE_MODEL
    products: list[_SwiggyProduct]


class _SwiggyFoodVariantOption(BaseModel):
    model_config = _FIXTURE_MODEL
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    inStock: int = 1
    default: int = 0


class _SwiggyFoodVariantGroup(BaseModel):
    model_config = _FIXTURE_MODEL
    groupId: str
    name: str
    variations: list[_SwiggyFoodVariantOption] = Field(default_factory=list)


class _SwiggyFoodMenuItem(BaseModel):
    model_config = _FIXTURE_MODEL
    name: str = Field(min_length=1)
    # Rupees, not paise — same convention as Instamart, see normalize().
    # float, not int: a real live search_menu response had a fractional
    # price (a discounted item), which `int` strictly rejected outright —
    # found by reproducing the actual failure, not assumed upfront.
    price: float = Field(ge=0)
    menu_item_id: str = Field(min_length=1)
    inStock: int = 1
    hasVariants: bool = False
    variantsV2: list[_SwiggyFoodVariantGroup] = Field(default_factory=list)
    imageUrl: str = ""


class _SwiggyFoodSearch(BaseModel):
    model_config = _FIXTURE_MODEL
    items: list[_SwiggyFoodMenuItem] = Field(default_factory=list)


class SwiggyNormalizer:
    """Fixture: Instamart ``search_products``, captured from a real live call
    (2026-08-31, address in Electronic City — see docs/CONNECTORS.md) rather
    than assumed. The shape is nothing like a flat item list: every product
    carries one or more *variations* (pack sizes), and price/availability
    live on the variation, not the product — a 500ml pack and a 500ml×4 pack
    of the same milk are different SKUs at different prices. One
    ``ScoredOffer`` is emitted per variation, not per product.

    ``offerPrice`` is rupees (e.g. ``108`` for a real ₹108 item), not paise —
    confirmed against real catalog prices, since 108 paise (~₹1.08) for a
    500ml×4 milk pack would be nonsensical. Converted to this project's
    integer-paise money contract (docs/API_CONTRACTS.md #1) by ``* 100``.

    Food's ``search_menu``, captured from a real live call (2026-09-01,
    ``mcp.swiggy.com/food``, address in Electronic City). Same rupees-not-
    paise money contract as Instamart. Unlike Instamart's flat
    one-variation-per-SKU shape, a Food menu item's real purchasable unit is
    a COMBINATION across multiple variant groups (Crust × Size, etc.) —
    ``update_food_cart``'s real argument shape for naming one specific
    combination has not been verified, so building a variant_id that claims
    to be addable to a cart would be a guess this project's own rules
    forbid. Only items where ``hasVariants`` is false — where
    ``menu_item_id`` alone already names one unambiguous purchasable thing —
    are normalized into an ``Offer``; a multi-variant dish is real and
    visible in the raw payload, but is not represented as a candidate here
    until that cart-write shape is verified the same way Instamart's was.

    ``search_restaurants`` returns venues, not purchasable items (no price
    for a specific dish) — the same "informational, not an offer list, not
    a failure" treatment as ``get_addresses``, below.

    Food/menu shapes beyond these two are deliberately not guessed at. They
    stay unsupported until their own harmless live result has been captured.

    ``get_addresses`` and ``get_cart`` return ``None``, not an error. Real
    incident: Swiggy's own ``search_products`` tool description *requires*
    calling ``get_addresses`` first to obtain a valid ``addressId`` — "You
    MUST call get_addresses first... NEVER guess, invent, or use placeholder
    values" — so a real live mission correctly calls it before searching.
    Treating that successful, mandated prerequisite call as an "unsupported
    Swiggy fixture" (which the R0-tier `ConnectorPayloadError` it used to
    raise did) broke every real mission before it ever reached the search
    that actually matters. Neither tool returns buyable offers, so there is
    nothing to normalize into a ``CommerceResult`` — but succeeding at them
    is not a failure, and must not be reported as one.
    """

    # get_addresses is shared across Instamart and Food — verified directly
    # against the real Food MCP tool's own description ("This tool works
    # for Swiggy Instamart and Food services"). get_cart/get_food_cart are
    # each that vertical's own prerequisite read; search_restaurants returns
    # venues, not priced items. None of the four return buyable offers, so
    # none of them are a fixture this normalizer needs to parse — but
    # calling one must never be reported as a failure.
    _INFORMATIONAL_ONLY = frozenset({"get_addresses", "get_cart", "get_food_cart", "search_restaurants"})
    _INFORMATIONAL_CONNECTORS = frozenset({"swiggy-instamart", "swiggy-food"})

    def normalize(
        self, call: ToolCallEvent, *, budget_minor: int | None = None
    ) -> CommerceResult | None:
        if call.connector_id in self._INFORMATIONAL_CONNECTORS and call.tool_name in self._INFORMATIONAL_ONLY:
            return None
        if call.connector_id == "swiggy-food" and call.tool_name == "search_menu":
            return self._normalize_food_menu(call, budget_minor=budget_minor)
        if call.connector_id != "swiggy-instamart" or call.tool_name != "search_products":
            raise ConnectorPayloadError(call.connector_id, call.tool_name, "unsupported Swiggy fixture")
        data = _decoded_payload(call)
        try:
            body = _SwiggySearch.model_validate(data)
        except ValidationError as exc:
            raise _validation_error(call, exc) from None

        offers: list[ScoredOffer] = []
        for product in body.products:
            for variation in product.variations:
                offer = Offer(
                    store=call.connector_id,
                    store_label="Swiggy Instamart",
                    product_id=product.productId,
                    variant_id=variation.spinId,
                    title=product.displayName,
                    variant_title=variation.quantityDescription,
                    price_minor=variation.price.offerPrice * 100,
                    currency="INR",
                    available=variation.isInStockAndAvailable and product.inStock and product.isAvail,
                    image=variation.imageUrl,
                )
                offers.append(_scored(offer, budget_minor=budget_minor))
        return CommerceResult(merchant=call.connector_id, offers=offers)

    def _normalize_food_menu(
        self, call: ToolCallEvent, *, budget_minor: int | None,
    ) -> CommerceResult:
        data = _decoded_payload(call)
        try:
            body = _SwiggyFoodSearch.model_validate(data)
        except ValidationError as exc:
            raise _validation_error(call, exc) from None

        offers: list[ScoredOffer] = []
        for item in body.items:
            # See the class docstring: a variant-having dish's real
            # purchasable unit is a combination across multiple variant
            # groups, and update_food_cart's argument shape for naming one
            # has not been verified — so it is not represented as a
            # candidate here. Real (visible in the raw payload) but not
            # normalized, not silently dropped as if it didn't exist.
            if item.hasVariants:
                continue
            offer = Offer(
                store=call.connector_id,
                store_label="Swiggy Food",
                product_id=item.menu_item_id,
                variant_id=item.menu_item_id,
                title=item.name,
                price_minor=round(item.price * 100),
                currency="INR",
                available=item.inStock > 0,
                image=item.imageUrl,
            )
            offers.append(_scored(offer, budget_minor=budget_minor))
        return CommerceResult(merchant=call.connector_id, offers=offers)


class _GitHubUser(BaseModel):
    model_config = _FIXTURE_MODEL
    login: str


class _GitHubIssue(BaseModel):
    model_config = _FIXTURE_MODEL
    number: int
    title: str = Field(min_length=1)
    state: str
    html_url: str
    user: _GitHubUser


class _GitHubIssues(BaseModel):
    model_config = _FIXTURE_MODEL
    issues: list[_GitHubIssue]


class GitHubNormalizer:
    """Fixture: hosted GitHub MCP issue list, wrapped as ``{"issues": [...]}``."""

    def normalize(self, call: ToolCallEvent, *, budget_minor: int | None = None) -> DevTaskResult:
        if call.tool_name != "list_issues":
            raise ConnectorPayloadError(call.connector_id, call.tool_name, "unsupported operation")
        data = _decoded_payload(call)
        if isinstance(data, list):
            data = {"issues": data}
        try:
            body = _GitHubIssues.model_validate(data)
        except ValidationError as exc:
            raise _validation_error(call, exc) from None
        return DevTaskResult(
            source="github",
            items=[
                {
                    "number": issue.number,
                    "title": issue.title,
                    "state": issue.state,
                    "url": issue.html_url,
                    "author": issue.user.login,
                }
                for issue in body.issues
            ],
        )


def normalize(
    call: ToolCallEvent, *, capability: str, risk_tier: str, provenance: str,
    budget_minor: int | None = None,
) -> ConnectorResult | None:
    """Returns ``None`` when the call succeeded but is purely informational
    (e.g. Swiggy's mandatory ``get_addresses`` lookup before a search) — a
    real, successful step with nothing offer-shaped to report, not a
    failure. Callers must skip a ``None`` result, not treat its absence as
    an error."""
    if not call.succeeded:
        raise ConnectorPayloadError(
            call.connector_id, call.tool_name,
            "tool result missing" if call.result is None else "tool returned an error",
        )

    normalizer: _Normalizer
    if call.connector_id == "shopify":
        normalizer = ShopifyNormalizer()
    elif call.connector_id.startswith("swiggy-"):
        normalizer = SwiggyNormalizer()
    elif call.connector_id == "github":
        normalizer = GitHubNormalizer()
    else:
        raise ConnectorPayloadError(call.connector_id, call.tool_name, "no normalizer registered")

    payload = normalizer.normalize(call, budget_minor=budget_minor)
    if payload is None:
        return None
    return ConnectorResult(
        connector_id=call.connector_id,
        capability=capability,
        operation=call.tool_name,
        risk_tier=risk_tier,
        execution_id=call.execution_id,
        observed_at=datetime.now(timezone.utc),
        provenance=provenance,
        payload=payload,
    )


def _decoded_payload(call: ToolCallEvent) -> Any:
    raw = call.result
    if isinstance(raw, str):
        return _json_text(call, raw)
    if (
        isinstance(raw, list)
        and len(raw) == 1
        and isinstance(raw[0], dict)
        and raw[0].get("type") == "text"
        and isinstance(raw[0].get("text"), str)
    ):
        return _json_text(call, raw[0]["text"])
    return raw


def _json_text(call: ToolCallEvent, text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorPayloadError(call.connector_id, call.tool_name, "result text was not JSON") from exc


def _validation_error(call: ToolCallEvent, exc: ValidationError) -> ConnectorPayloadError:
    first = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "payload"
    return ConnectorPayloadError(
        call.connector_id, call.tool_name,
        f"payload did not match fixture at {location}: {first.get('msg', 'invalid')}",
    )


def _scored(offer: Offer, *, budget_minor: int | None = None) -> ScoredOffer:
    return ScoredOffer(
        offer=offer,
        relevance=1.0,
        in_stock=offer.available,
        priced=True,
        within_budget=None if budget_minor is None else offer.price_minor <= budget_minor,
        line_total_minor=offer.price_minor,
    )
