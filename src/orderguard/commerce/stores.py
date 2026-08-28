"""The stores we can actually shop.

Every domain here was probed and answered with the full Shopify Storefront MCP
toolset — search AND cart. Nothing is listed on the strength of it being a
well-known brand; ``probe/wide.py`` re-runs the check.

The list started at five niche food brands, which is why the app returned
nothing for almost anything a person would actually buy (F-021). Widening it is
not cosmetic: it is the difference between "I cannot help with that" and a real
comparison.

Domains that answered but cannot take a cart, or did not answer at all, are in
``KNOWN_BAD`` with the reason. Recording the failure stops the next person
re-testing them hopefully.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = [
    "Store", "GROCERY", "DRINKS", "BEAUTY", "HEALTH", "LIFESTYLE",
    "ALL", "SHOPPABLE", "by_domain", "for_query", "KNOWN_BAD",
]


class Store(NamedTuple):
    domain: str
    label: str
    kind: str          # grocery | drinks | beauty | health | lifestyle
    sells: str = ""    # plain words, used to route a query to likely shops


GROCERY: tuple[Store, ...] = (
    Store("slurrpfarm.com", "Slurrp Farm", "grocery",
          "millet cereal porridge kids breakfast pancake atta noodles"),
    Store("nourishyou.in", "Nourish You", "grocery",
          "quinoa chia seeds oats superfood granola milk"),
    Store("twobrothersindiashop.com", "Two Brothers", "grocery",
          "ghee atta jaggery honey oil pulses organic"),
    Store("farmley.com", "Farmley", "grocery",
          "dates nuts cashew almond dry fruits makhana snacks"),
    Store("opensecret.in", "Open Secret", "grocery",
          "nut chips cookies namkeen snacks chocolate"),
    Store("happilo.com", "Happilo", "grocery",
          "dry fruits nuts cashew almond raisin seeds berries"),
    Store("24mantraorganic.com", "24 Mantra Organic", "grocery",
          "organic rice atta dal spices oil sugar"),
    Store("praakritik.com", "Praakritik", "grocery",
          "organic staples rice dal spices honey"),
    Store("sprig.co.in", "Sprig", "grocery",
          "baking syrup sauce vanilla dessert gourmet"),
    Store("wellversed.in", "Wellversed", "grocery",
          "keto protein diet peanut butter low carb"),
)

DRINKS: tuple[Store, ...] = (
    Store("bluetokaicoffee.com", "Blue Tokai", "drinks",
          "coffee beans filter espresso cold brew"),
    Store("sleepyowl.co", "Sleepy Owl", "drinks",
          "coffee cold brew instant hot brew"),
    Store("teabox.com", "Teabox", "drinks",
          "tea darjeeling assam green chai masala"),
)

BEAUTY: tuple[Store, ...] = (
    Store("mamaearth.in", "Mamaearth", "beauty",
          "shampoo face wash cream baby skin hair"),
    Store("mcaffeine.com", "mCaffeine", "beauty",
          "coffee scrub body wash face serum"),
    Store("plumgoodness.com", "Plum", "beauty",
          "moisturiser serum face wash sunscreen"),
    Store("dotandkey.com", "Dot & Key", "beauty",
          "serum sunscreen moisturiser face mask"),
    Store("sugarcosmetics.com", "SUGAR Cosmetics", "beauty",
          "lipstick kajal foundation makeup"),
    Store("bombayshavingcompany.com", "Bombay Shaving Company", "beauty",
          "razor shaving beard trimmer grooming"),
    Store("beardo.in", "Beardo", "beauty",
          "beard oil wax perfume grooming men"),
    Store("juicychemistry.com", "Juicy Chemistry", "beauty",
          "organic soap oil serum natural skincare"),
    Store("earthrhythm.com", "Earth Rhythm", "beauty",
          "shampoo bar cleanser sunscreen lipstick"),
)

HEALTH: tuple[Store, ...] = (
    Store("traya.health", "Traya", "health", "hair loss treatment ayurveda"),
    Store("boldcare.in", "Bold Care", "health", "wellness supplement men health"),
)

LIFESTYLE: tuple[Store, ...] = (
    Store("boat-lifestyle.com", "boAt", "lifestyle",
          "earbuds headphones speaker smartwatch charger airdopes"),
    Store("chumbak.com", "Chumbak", "lifestyle",
          "mug cushion decor bag home gift"),
    Store("zouk.co.in", "Zouk", "lifestyle", "bag laptop sleeve wallet tote"),
    Store("nappadori.com", "Nappa Dori", "lifestyle", "leather bag wallet trunk"),
    Store("houseofchikankari.in", "House of Chikankari", "lifestyle",
          "kurta suit saree chikankari clothing"),
)

ALL: tuple[Store, ...] = GROCERY + DRINKS + BEAUTY + HEALTH + LIFESTYLE
SHOPPABLE = ALL

_BY_DOMAIN = {s.domain: s for s in ALL}


def by_domain(domain: str) -> Store:
    try:
        return _BY_DOMAIN[domain]
    except KeyError:
        raise KeyError(f"unknown store: {domain!r}") from None


def for_query(query: str, limit: int = 8) -> tuple[Store, ...]:
    """Pick the shops most likely to stock this, by plain word overlap.

    Searching all twenty-four every time is slow and mostly wasted — a coffee
    roaster has no opinion about lipstick. Shops whose ``sells`` words overlap
    the request go first; if none do, everything is searched, because a bad
    guess here must never be the reason something is not found.
    """
    words = {w for w in query.lower().split() if len(w) > 2}
    if not words:
        return ALL[:limit]

    scored = [
        (len(words & set(store.sells.split())), store)
        for store in ALL
    ]
    matched = [store for score, store in scored if score]
    if not matched:
        return ALL                    # no idea: ask everyone
    matched.sort(key=lambda s: -len(words & set(s.sells.split())))
    return tuple(matched[:limit])


# Probed and unusable. Kept so the list is evidence rather than folklore.
KNOWN_BAD: dict[str, str] = {
    "thewholetruthfoods.com": "405 — endpoint refuses POST",
    "yogabar.in": "403",
    "nutrabay.com": "404 — no MCP endpoint",
    "trueelements.co.in": "404",
    "rostaa.com": "404",
    "kapiva.in": "404",
    "noise.com": "404",
    "thesouledstore.com": "404",
    "urbanladder.com": "404",
    "nestasia.in": "403",
    "bewakoof.com": "503",
    "foxtale.in": "answered, but not with JSON",
    "minimalist.co": "search only — no cart tools",
    "arata.in": "search only — no cart tools",
    "setu.in": "search only — no cart tools",
    "ellementry.com": "search only — no cart tools",
    "licious.in": "not a Shopify storefront",
    "countrydelight.in": "404",
    "freshtohome.com": "not a Shopify storefront",
    "paperboat.com": "404",
    "idfreshfood.com": "404",
}
