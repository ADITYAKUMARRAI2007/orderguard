"""Search the open web for products, alongside the stores we can shop directly.

Some shops we can buy from (Shopify, over MCP). Most we cannot — Amazon,
Flipkart, Myntra, Nykaa. Refusing to look at them makes the comparison worse for
no gain, so this module searches the web, pulls out prices where they are
visible, and shows them next to the offers we can actually transact with.

Two rules make that safe.

**A web result is never something you can buy.** It is a link and a claimed
price. ``WebResult`` deliberately has no variant id, no cart method, and no path
into ``CartExpectation``. The only thing the UI can do with one is show it and
open it. That is not a limitation to be lifted later — a price scraped from a
search snippet has no merchant behind it and must never reach a gate.

**Search results are attacker-controlled text.** Anyone can rank a page for
"millet cereal" and fill it with instructions aimed at an AI. This module reads
titles, links and snippets as strings to display, and none of it reaches a
decision. Same rule as merchant prose (D-023); the surface is just wider.

Providers are pluggable and the whole thing degrades to nothing gracefully: with
no key configured, searching returns an empty result with a reason, and the app
carries on with the stores it can shop.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "WebResult",
    "WebSearchOutcome",
    "SearchProvider",
    "SerperProvider",
    "BraveProvider",
    "StubSearchProvider",
    "NoSearchProvider",
    "provider_from_env",
    "search_web",
    "price_from_text",
]

STRICT = ConfigDict(extra="forbid")

# ₹1,299  |  Rs. 1299.00  |  INR 1,299
_PRICE = re.compile(
    r"(?:₹|\bRs\.?\s*|\bINR\s*)\s*([0-9][0-9,]*)(?:\.([0-9]{1,2}))?", re.IGNORECASE
)

_KNOWN_SHOPS = {
    "amazon.in": "Amazon", "amazon.com": "Amazon", "flipkart.com": "Flipkart",
    "myntra.com": "Myntra", "nykaa.com": "Nykaa", "ajio.com": "AJIO",
    "meesho.com": "Meesho", "tatacliq.com": "Tata CLiQ", "bigbasket.com": "BigBasket",
    "blinkit.com": "Blinkit", "zeptonow.com": "Zepto", "jiomart.com": "JioMart",
}


def price_from_text(text: str) -> int | None:
    """Pull a rupee price out of a snippet, as integer paise.

    Returns ``None`` rather than guessing. A wrong price shown next to real ones
    is worse than no price at all, because it looks equally authoritative.
    """
    match = _PRICE.search(text or "")
    if match is None:
        return None
    rupees = match.group(1).replace(",", "")
    if not rupees.isdigit():
        return None
    paise = int(rupees) * 100
    if match.group(2):
        paise += int(match.group(2).ljust(2, "0"))
    return paise


def _site_of(url: str) -> tuple[str, str]:
    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    host = host.removeprefix("www.")
    for domain, label in _KNOWN_SHOPS.items():
        if host == domain or host.endswith("." + domain):
            return domain, label
    return host, host.split(".")[0].title() if host else ""


class WebResult(BaseModel):
    """A product seen on the open web. Look, compare, open — never buy from here.

    Note the fields that are absent: no variant id, no availability, nothing a
    cart could consume. A price read out of a snippet is a claim, not an offer.
    """

    model_config = STRICT

    title: str
    url: str
    site: str
    site_label: str = ""
    snippet: str = ""
    claimed_price_paise: int | None = None

    @property
    def shoppable_here(self) -> bool:
        """Always false. Present so the UI never has to decide."""
        return False


class WebSearchOutcome(BaseModel):
    model_config = STRICT

    query: str
    provider: str
    results: list[WebResult] = Field(default_factory=list)
    unavailable_reason: str = ""

    @property
    def worked(self) -> bool:
        return not self.unavailable_reason


# --- providers --------------------------------------------------------------

class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int) -> list[dict]:
        """Return raw ``{title, link, snippet}`` dicts."""
        ...


class NoSearchProvider:
    """What you get with no key. Says so instead of pretending."""

    name = "none"

    async def search(self, query: str, limit: int) -> list[dict]:
        raise RuntimeError(
            "No web search key configured. Set SEARCH_PROVIDER and SEARCH_API_KEY "
            "in .env (serper.dev or brave, both have free tiers). Store search "
            "works without it."
        )


class SerperProvider:
    """serper.dev — Google results, free tier, no card."""

    name = "serper"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def search(self, query: str, limit: int) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://google.serper.dev/shopping",
                headers={"X-API-KEY": self._key, "Content-Type": "application/json"},
                json={"q": query, "gl": "in", "hl": "en", "num": limit},
            )
            response.raise_for_status()
            body = response.json()

        # The shopping endpoint gives structured prices; organic is the fallback.
        items = body.get("shopping") or body.get("organic") or []
        return [
            {
                "title": str(item.get("title") or ""),
                "link": str(item.get("link") or ""),
                "snippet": str(item.get("snippet") or item.get("price") or ""),
            }
            for item in items[:limit]
        ]


class BraveProvider:
    """Brave Search API — independent index, free tier."""

    name = "brave"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def search(self, query: str, limit: int) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
                params={"q": query, "country": "IN", "count": limit},
            )
            response.raise_for_status()
            body = response.json()

        return [
            {
                "title": str(item.get("title") or ""),
                "link": str(item.get("url") or ""),
                "snippet": str(item.get("description") or ""),
            }
            for item in (body.get("web") or {}).get("results", [])[:limit]
        ]


class StubSearchProvider:
    """Deterministic results so the suite never needs the network."""

    name = "stub"

    def __init__(self, answers: list[dict] | None = None) -> None:
        self._answers = answers if answers is not None else [
            {
                "title": "Farmley Premium Cashews 200g",
                "link": "https://www.amazon.in/dp/B08EXAMPLE",
                "snippet": "Farmley Cashews 200g. ₹310.00. Free delivery.",
            },
            {
                "title": "Roasted Salted Cashews 250g",
                "link": "https://www.flipkart.com/cashew/p/itmEXAMPLE",
                "snippet": "Special price Rs. 289 with bank offer.",
            },
        ]

    async def search(self, query: str, limit: int) -> list[dict]:
        return self._answers[:limit]


def provider_from_env() -> SearchProvider:
    """Pick a provider from .env. Missing key is a normal state, not a crash."""
    chosen = (os.environ.get("SEARCH_PROVIDER") or "").strip().lower()
    key = (os.environ.get("SEARCH_API_KEY") or "").strip()

    if chosen == "stub":
        return StubSearchProvider()
    if not key:
        return NoSearchProvider()
    if chosen == "serper":
        return SerperProvider(key)
    if chosen == "brave":
        return BraveProvider(key)
    return NoSearchProvider()


# --- the search itself ------------------------------------------------------

async def search_web(
    query: str,
    *,
    provider: SearchProvider | None = None,
    limit: int = 6,
    shopping_terms: str = "price buy online India",
) -> WebSearchOutcome:
    """Search the open web for a product. Never raises."""
    provider = provider or provider_from_env()
    phrased = f"{query} {shopping_terms}".strip()

    try:
        raw = await provider.search(phrased, limit)
    except Exception as exc:                    # noqa: BLE001 - a dead search is not a dead app
        return WebSearchOutcome(
            query=query, provider=provider.name, unavailable_reason=str(exc)
        )

    results: list[WebResult] = []
    for item in raw:
        url = str(item.get("link") or "")
        if not url.startswith("http"):
            continue
        site, label = _site_of(url)
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not title:
            continue

        results.append(
            WebResult(
                title=title[:200],
                url=url,
                site=site,
                site_label=label,
                # Truncated because it is shown, and because a long snippet is
                # just more room for text aimed at a model. It reaches no gate
                # either way.
                snippet=snippet[:300],
                claimed_price_paise=price_from_text(f"{title} {snippet}"),
            )
        )

    return WebSearchOutcome(query=query, provider=provider.name, results=results)
