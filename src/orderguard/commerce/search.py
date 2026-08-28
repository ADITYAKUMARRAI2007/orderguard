"""Search several real stores at once and rank what comes back.

Two things this module refuses to do.

**It does not invent a trust score.** "Find me a reliable store" is a reasonable
thing to say and an unreasonable thing to compute. We cannot measure whether a
merchant honours refunds. Inventing a 0-100 number would look authoritative and
mean nothing, which is worse than saying nothing. So ``reliable`` is replaced by
four facts we can actually observe, each recorded per offer:

* the store answered
* the item says it is in stock
* the item has a real price
* the title genuinely overlaps what was asked for

That list is shown to the user as-is. It is checkable; a score would not be.

**It does not choose.** ``rank`` puts the best candidate first and says why in
words, but picking is the user's. Choosing the cheapest of several products is a
spending decision, and this project's whole argument is that an agent should not
make those alone. Cheapest is often a smaller pack.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .base import AdapterError, Location, Offer
from .shopify_mcp import ShopifyMCPAdapter
from .stores import GROCERY, Store

__all__ = ["ScoredOffer", "SearchOutcome", "search_stores", "rank"]

STRICT = ConfigDict(extra="forbid")

_STOPWORDS = frozenset(
    "a an and the for of to me my some get buy order i want need please "
    "under below within rs rupees rupee".split()
)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


class ScoredOffer(BaseModel):
    """An offer plus the evidence behind its position in the list."""

    model_config = STRICT

    offer: Offer
    relevance: float = Field(ge=0.0, le=1.0)
    in_stock: bool
    priced: bool
    within_budget: bool | None = None      # None when no budget was given
    line_total_minor: int = Field(ge=0)

    @property
    def signals(self) -> list[str]:
        """Plain-language facts, for showing the user."""
        out = [f"{self.offer.store_label} answered"]
        out.append("in stock" if self.in_stock else "out of stock")
        if self.priced:
            out.append("price quoted")
        if self.within_budget is False:
            out.append("over your limit")
        return out


class SearchOutcome(BaseModel):
    """Everything one search produced, including what went wrong."""

    model_config = STRICT

    query: str
    quantity: int = Field(ge=1)
    budget_minor: int | None = None
    searched_from: str = ""
    offers: list[ScoredOffer] = Field(default_factory=list)
    stores_searched: list[str] = Field(default_factory=list)
    stores_failed: dict[str, str] = Field(default_factory=dict)

    @property
    def any_results(self) -> bool:
        return bool(self.offers)

    @property
    def needs_a_choice(self) -> bool:
        """True when a human should pick, rather than the agent assuming.

        Two or more usable candidates means a real decision exists.
        """
        usable = [o for o in self.offers if o.in_stock and o.relevance > 0]
        return len(usable) > 1


async def _search_one(
    store: Store, query: str, client: httpx.AsyncClient, limit: int,
    location: Location | None = None,
) -> tuple[Store, list[Offer] | str]:
    """Never raises. A broken store must not take the others down with it."""
    adapter = ShopifyMCPAdapter(store.domain, store.label, client=client)
    try:
        return store, await adapter.search(query, limit=limit, location=location)
    except AdapterError as exc:
        return store, str(exc)
    except Exception as exc:                    # noqa: BLE001 - isolation is the point
        return store, f"{type(exc).__name__}: {exc}"


async def search_stores(
    query: str,
    *,
    quantity: int = 1,
    budget_minor: int | None = None,
    stores: tuple[Store, ...] = GROCERY,
    limit_per_store: int = 5,
    location: Location | None = None,
) -> SearchOutcome:
    """Hit every store at the same time, then rank the combined results."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        results = await asyncio.gather(
            *(_search_one(s, query, client, limit_per_store, location) for s in stores)
        )

    outcome = SearchOutcome(
        query=query, quantity=quantity, budget_minor=budget_minor,
        searched_from=location.described if location else "",
    )
    wanted = _tokens(query)

    for store, result in results:
        if isinstance(result, str):
            outcome.stores_failed[store.label] = result
            continue

        outcome.stores_searched.append(store.label)
        for offer in result:
            found = _tokens(f"{offer.title} {offer.variant_title}")
            relevance = len(wanted & found) / len(wanted) if wanted else 1.0
            line_total = offer.total_minor(quantity)

            outcome.offers.append(
                ScoredOffer(
                    offer=offer,
                    relevance=round(relevance, 3),
                    in_stock=offer.available,
                    priced=offer.price_minor > 0,
                    within_budget=(
                        None if budget_minor is None else line_total <= budget_minor
                    ),
                    line_total_minor=line_total,
                )
            )

    outcome.offers = rank(outcome.offers)
    return outcome


def rank(offers: list[ScoredOffer]) -> list[ScoredOffer]:
    """Best first. Sorted purely on observable facts, in a stated order.

    1. in stock beats out of stock
    2. within budget beats over budget
    3. more relevant to the words used
    4. cheaper for the quantity asked
    5. store name, so equal candidates never shuffle between runs
    """
    return sorted(
        offers,
        key=lambda s: (
            not s.in_stock,
            s.within_budget is False,
            -s.relevance,
            s.line_total_minor,
            s.offer.store,
        ),
    )
