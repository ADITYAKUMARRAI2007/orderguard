"""Every agent-commerce surface we know of, and its real status.

This is a directory, not a marketing page. A connector appears here whether or
not we can use it, because the useful information is often that we *cannot* —
and why.

Four statuses, and the difference between them matters:

``LIVE``          verified working. Searched, wrote a cart, read it back.
``NEEDS_ACCESS``  a real official endpoint that requires approval we do not have.
``RESTRICTED``    official, but their terms forbid what we would be doing.
``UNAVAILABLE``   no public agent endpoint exists.

Every entry carries the evidence and the date it was checked, so a reader can
re-run the check rather than take our word for it. Twice during this project I
concluded something did not exist because a search did not surface it, and was
wrong both times (FAILURE_LOG F-004, F-009). Recording the probe instead of the
conclusion is the fix for that.

**On the ones we refuse.** Unofficial, reverse-engineered servers exist for some
Indian quick-commerce apps. They are excluded on purpose. They break the
platform's terms, they place real orders with real money, and a safety product
that begins by violating a merchant's rules has argued against itself before it
starts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from .commerce.stores import ALL as VERIFIED_STORES

__all__ = ["Status", "Connector", "CONNECTORS", "by_id", "live_connectors", "summary"]


class Status(StrEnum):
    LIVE = "live"
    NEEDS_ACCESS = "needs_access"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class Connector(NamedTuple):
    id: str
    label: str
    kind: str                 # "grocery" | "food" | "general" | "payments"
    status: Status
    protocol: str             # what it speaks, if anything
    evidence: str             # what we actually observed
    checked_on: str
    can_order: bool           # would OrderGuard place an order through it today
    note: str = ""


_CHECKED = "2026-08-28"


# --- working today ---------------------------------------------------------

_SHOPIFY = tuple(
    Connector(
        id=store.domain,
        label=store.label,
        kind=store.kind,
        status=Status.LIVE,
        protocol="Shopify Storefront MCP",
        evidence=(
            f"POST https://{store.domain}/api/mcp returned a catalogue with "
            "INR prices; cart written and read back independently"
        ),
        checked_on="2026-08-27",
        can_order=False,
        note="Guarded cart only. We build and verify the cart, then hand you the "
             "checkout link. We never complete a purchase on someone else's store.",
    )
    for store in VERIFIED_STORES
)


# --- real, but we cannot use them ------------------------------------------

_GATED = (
    Connector(
        id="swiggy",
        label="Swiggy",
        kind="food",
        status=Status.NEEDS_ACCESS,
        protocol="Swiggy MCP (Food, Instamart, Dineout)",
        evidence=(
            "POST https://mcp.swiggy.com returned HTTP 401. The server is real "
            "and live; it requires credentials we do not have. Access is via "
            "Swiggy Builders Club: application, whitelist, partner contract."
        ),
        checked_on=_CHECKED,
        can_order=False,
        note="Official and genuinely capable — including order placement and "
             "payment. Two blockers: access is an approval process, and there "
             "is no sandbox, so every order would be a real order with real "
             "money. This project is test-mode only.",
    ),
    Connector(
        id="zomato",
        label="Zomato",
        kind="food",
        status=Status.RESTRICTED,
        protocol="Zomato MCP",
        evidence=(
            "No public endpoint resolved at mcp.zomato.com. A manifest exists at "
            "github.com/Zomato/mcp-server-manifest, and its terms state that "
            "third-party app development is explicitly prohibited; access is "
            "described as personal use only."
        ),
        checked_on=_CHECKED,
        can_order=False,
        note="Excluded by their rules, not by our capability. We are not going "
             "to build against a platform that has said not to.",
    ),
)


# --- no public agent surface ------------------------------------------------

_ABSENT = tuple(
    Connector(
        id=cid, label=label, kind="grocery", status=Status.UNAVAILABLE,
        protocol="none published",
        evidence="No official public agent API or MCP endpoint found.",
        checked_on=_CHECKED,
        can_order=False,
        note="Unofficial reverse-engineered servers circulate for some of these. "
             "We do not use them: they break the platform's terms and they spend "
             "real money.",
    )
    for cid, label in (
        ("zepto", "Zepto"),
        ("blinkit", "Blinkit"),
        ("bigbasket", "BigBasket"),
        ("instamart", "Swiggy Instamart (standalone)"),
    )
)


# --- money ------------------------------------------------------------------

_PAYMENTS = (
    Connector(
        id="razorpay",
        label="Razorpay",
        kind="payments",
        status=Status.LIVE,
        protocol="Razorpay REST API, test mode",
        evidence=(
            "Test order created, checkout completed, payment captured and then "
            "verified server-side: constant-time signature check, independent "
            "fetch, equality on status, amount, currency and order id."
        ),
        checked_on="2026-08-26",
        can_order=True,
        note="Test mode only. No live credentials, and no real money at any "
             "point in this project.",
    ),
)


CONNECTORS: tuple[Connector, ...] = _SHOPIFY + _GATED + _ABSENT + _PAYMENTS

_BY_ID = {c.id: c for c in CONNECTORS}


def by_id(connector_id: str) -> Connector:
    try:
        return _BY_ID[connector_id]
    except KeyError:
        raise KeyError(f"unknown connector: {connector_id!r}") from None


def live_connectors(kind: str | None = None) -> tuple[Connector, ...]:
    """The ones OrderGuard can actually shop from right now."""
    return tuple(
        c for c in CONNECTORS
        if c.status is Status.LIVE and c.kind != "payments"
        and (kind is None or c.kind == kind)
    )


def summary() -> dict[str, int]:
    counts = {status.value: 0 for status in Status}
    for connector in CONNECTORS:
        counts[connector.status.value] += 1
    return counts
