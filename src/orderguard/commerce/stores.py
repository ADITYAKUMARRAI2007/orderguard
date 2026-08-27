"""The stores we shop across.

Every one of these was checked by hand: the MCP endpoint answered, returned a
real catalogue, and quoted prices in INR. Domains that redirected, 404'd or
refused are listed at the bottom so nobody re-tests them hoping for a different
answer.

Adding a store is one line. That is the whole point of the adapter — nothing
about a shop is hardcoded anywhere else.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["Store", "GROCERY", "GENERAL", "ALL", "by_domain", "KNOWN_BAD"]


class Store(NamedTuple):
    domain: str
    label: str
    kind: str          # "grocery" | "general"


GROCERY: tuple[Store, ...] = (
    Store("slurrpfarm.com",          "Slurrp Farm",        "grocery"),
    Store("nourishyou.in",           "Nourish You",        "grocery"),
    Store("twobrothersindiashop.com", "Two Brothers",      "grocery"),
    Store("bluetokaicoffee.com",     "Blue Tokai",         "grocery"),
    Store("sleepyowl.co",            "Sleepy Owl",         "grocery"),
)

GENERAL: tuple[Store, ...] = (
    Store("boat-lifestyle.com",  "boAt",           "general"),
    Store("mamaearth.in",        "Mamaearth",      "general"),
    Store("sugarcosmetics.com",  "SUGAR Cosmetics", "general"),
    Store("mcaffeine.com",       "mCaffeine",      "general"),
    Store("plumgoodness.com",    "Plum",           "general"),
    Store("chumbak.com",         "Chumbak",        "general"),
)

ALL: tuple[Store, ...] = GROCERY + GENERAL

_BY_DOMAIN = {s.domain: s for s in ALL}


def by_domain(domain: str) -> Store:
    try:
        return _BY_DOMAIN[domain]
    except KeyError:
        raise KeyError(f"unknown store: {domain!r}") from None


# Checked and unusable. Kept so the list is evidence, not folklore.
KNOWN_BAD: dict[str, str] = {
    "thewholetruthfoods.com": "405 — endpoint refuses POST",
    "yogabar.in":             "301 — redirects away from /api/mcp",
    "licious.in":             "301 — not a Shopify storefront",
    "countrydelight.in":      "404 — no MCP endpoint",
    "freshtohome.com":        "302 — not a Shopify storefront",
    "paperboat.com":          "404 — no MCP endpoint",
    "idfreshfood.com":        "404 — no MCP endpoint",
}
