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

import asyncio
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

# SPELLINGS, not an allowlist. Search covers the whole web; this only fixes the
# capitalisation of shops whose names a domain cannot spell — "tatacliq.com"
# should read "Tata CLiQ", not "Tatacliq". Every other site is handled by
# _site_of, which derives a label from the page title or the domain. Nothing
# here decides which results are returned.
_SHOP_SPELLINGS = {
    "amazon.in": "Amazon", "amazon.com": "Amazon", "flipkart.com": "Flipkart",
    "myntra.com": "Myntra", "nykaa.com": "Nykaa", "ajio.com": "AJIO",
    "meesho.com": "Meesho", "tatacliq.com": "Tata CLiQ", "bigbasket.com": "BigBasket",
    "blinkit.com": "Blinkit", "zeptonow.com": "Zepto", "jiomart.com": "JioMart",
    "indiamart.com": "IndiaMART",
}

# Search engines put the shop's own name at the end of a title, after a
# separator: "Roasted Kaju 200g - Buy Salted Cashews | JEWEL FARMER".
# Reading it there beats guessing from a domain, which cannot tell that
# "jewelfarmer.com" is two words.
_TITLE_TAIL = re.compile(r"[|–—-]\s*([^|–—-]{2,40})\s*$")


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


def _site_of(url: str, title: str = "", source: str = "") -> tuple[str, str]:
    """Work out which shop a result is from, for any site on the web.

    Order of preference: the merchant the search engine named, then a spelling
    we know, then the shop's own name from the title, then the domain.

    ``source`` comes first because it is the only one that is not a guess. It
    also fixes the case where the link belongs to Google rather than the shop.
    """
    if source.strip():
        host = source.strip().lower().removeprefix("www.")
        return host, _SHOP_SPELLINGS.get(host, source.strip())

    host = re.sub(r"^https?://", "", url or "").split("/")[0].lower()
    host = host.removeprefix("www.")
    if not host:
        return "", ""

    for domain, label in _SHOP_SPELLINGS.items():
        if host == domain or host.endswith("." + domain):
            return domain, label

    stem = host.split(".")[0]
    tail = _TITLE_TAIL.search(title or "")
    if tail:
        candidate = tail.group(1).strip()
        # Only trust it when it plausibly names this site: the domain stem
        # should be in there once the spaces are removed. That rules out
        # titles ending in "Best Price in India".
        squashed = re.sub(r"[^a-z0-9]", "", candidate.lower())
        if squashed and (squashed in stem or stem in squashed):
            return host, candidate.title() if candidate.isupper() else candidate

    return host, stem.title()


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
    image: str = ""
    claimed_price_paise: int | None = None
    # None when no budget was stated, or when the page quoted no price at all.
    # A missing price is NOT "within budget" — we simply do not know.
    within_budget: bool | None = None
    line_total_paise: int | None = None

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
    budget_paise: int | None = None
    quantity: int = 1
    over_budget_count: int = 0
    unpriced_count: int = 0

    @property
    def worked(self) -> bool:
        return not self.unavailable_reason

    @property
    def budget_note(self) -> str:
        """One sentence about the budget, or nothing."""
        if self.budget_paise is None or not self.results:
            return ""
        within = sum(1 for r in self.results if r.within_budget)
        limit = f"₹{self.budget_paise / 100:,.2f}"
        if within and not self.over_budget_count:
            return f"All of these are within {limit}."
        if within:
            return (
                f"{within} of these are within {limit}. "
                f"{self.over_budget_count} cost more and are marked."
            )
        return (
            f"Nothing found is within {limit}. The cheapest is "
            f"₹{min(r.line_total_paise for r in self.results if r.line_total_paise) / 100:,.2f}."
        )


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
    """serper.dev — Google results, free tier, no card.

    Asks two endpoints, because neither alone is enough.

    ``/shopping`` gives a structured price and, crucially, a ``source`` field
    naming the real merchant — "Amazon.in", "Nutribinge". Its ``link`` is a
    google.com URL, so reading the shop from the link gives you "Google" for
    every single result (F-018).

    ``/search`` gives ordinary web results whose links ARE the merchant's own
    site, but with the price buried in prose.

    Both, merged, gives a real shop name, a real price, and a link that goes
    to the shop.
    """

    name = "serper"

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def _post(self, client: httpx.AsyncClient, path: str, query: str, limit: int):
        response = await client.post(
            f"https://google.serper.dev/{path}",
            headers={"X-API-KEY": self._key, "Content-Type": "application/json"},
            json={"q": query, "gl": "in", "hl": "en", "num": limit},
        )
        response.raise_for_status()
        return response.json()

    async def search(self, query: str, limit: int) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            shopping, organic = await asyncio.gather(
                self._post(client, "shopping", query, limit),
                self._post(client, "search", query, limit),
                return_exceptions=True,
            )

        results: list[dict] = []

        if isinstance(shopping, dict):
            # Everything the endpoint gives, not the first few. It returns
            # around forty; keeping six of them in Google's own order meant
            # discarding most of the catalogue before the budget was applied
            # (F-022).
            for item in (shopping.get("shopping") or []):
                results.append({
                    "title": str(item.get("title") or ""),
                    "link": str(item.get("link") or ""),
                    "snippet": str(item.get("price") or ""),
                    # the merchant, straight from Google rather than guessed
                    "source": str(item.get("source") or ""),
                    "image": str(item.get("imageUrl") or ""),
                })

        if isinstance(organic, dict):
            for item in (organic.get("organic") or []):
                results.append({
                    "title": str(item.get("title") or ""),
                    "link": str(item.get("link") or ""),
                    "snippet": str(item.get("snippet") or ""),
                    "source": "",
                    "image": "",
                })

        return results


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
    quantity: int = 1,
    budget_paise: int | None = None,
    shopping_terms: str = "price buy online India",
) -> WebSearchOutcome:
    """Search the open web for a product. Never raises.

    ``budget_paise`` is what the user said they would spend, for the whole
    quantity. Results within it are shown first and the rest are marked, rather
    than hidden — a shopper who asked for onions under ₹100 still wants to know
    the 10 kg sack is ₹460.
    """
    provider = provider or provider_from_env()
    phrased = f"{query} {shopping_terms}".strip()

    try:
        raw = await provider.search(phrased, max(limit, 20))
    except Exception as exc:                    # noqa: BLE001 - a dead search is not a dead app
        return WebSearchOutcome(
            query=query, provider=provider.name, unavailable_reason=str(exc),
            budget_paise=budget_paise, quantity=quantity,
        )

    results: list[WebResult] = []
    for item in raw:
        url = str(item.get("link") or "")
        if not url.startswith("http"):
            continue
        title = str(item.get("title") or "").strip()
        site, label = _site_of(url, title, str(item.get("source") or ""))
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
                image=str(item.get("image") or "")[:500],
                claimed_price_paise=price_from_text(f"{title} {snippet}"),
            )
        )

    # Shopping and organic often return the same product. Keep the first, which
    # is the shopping entry, because it carries the structured price.
    seen: set[str] = set()
    unique: list[WebResult] = []
    for result in results:
        key = re.sub(r"[^a-z0-9]", "", result.title.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)

    # Work out what each one costs for the quantity asked, and whether that
    # fits. A result with no price is left as None: not knowing is not the same
    # as being affordable, and marking it either way would be a guess.
    for result in unique:
        if result.claimed_price_paise is None:
            continue
        result.line_total_paise = result.claimed_price_paise * max(quantity, 1)
        if budget_paise is not None:
            result.within_budget = result.line_total_paise <= budget_paise

    # Affordable first, then cheapest, then the ones we could not price. Over
    # budget is shown, not hidden: someone asking for onions under Rs 100 still
    # wants to know the 10 kg sack exists at Rs 460.
    unique.sort(
        key=lambda r: (
            r.within_budget is False,
            r.line_total_paise is None,
            r.line_total_paise or 0,
        )
    )

    shown = unique[:limit]

    # Counted over what is SHOWN, not over everything fetched. Reporting "6 cost
    # more" beside six affordable rows is simply false, and a number the user
    # cannot see on screen is worse than no number.
    return WebSearchOutcome(
        query=query,
        provider=provider.name,
        results=shown,
        budget_paise=budget_paise,
        quantity=quantity,
        over_budget_count=sum(1 for r in shown if r.within_budget is False),
        unpriced_count=sum(1 for r in shown if r.claimed_price_paise is None),
    )
