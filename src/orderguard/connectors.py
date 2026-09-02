"""Every agent-commerce surface we know of, and its real status.

This is a directory, not a marketing page. A connector appears here whether or
not we can use it, because the useful information is often that we *cannot* —
and why.

**Two independent questions, two independent fields (2026-08-29 correction).**
An earlier version of this module collapsed "how capable is this connector"
and "how strongly have we verified it" into one five-value ``Status`` enum.
That conflates two different claims: Uber Eats and Instacart are both real
and both untested by us, but one can hand back an itemized cart and the other
cannot — the same ``AVAILABLE_UNTESTED`` label would have hidden that. Now:

``Evidence``   how strongly WE have verified this connector.
``Capability`` what the connector can actually do, independent of whether
               we've verified it.

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

__all__ = [
    "Evidence", "Capability", "ConnectorBackendType", "Connector", "CONNECTORS",
    "by_id", "live_connectors", "cart_capable_connectors", "summary",
]


class Evidence(StrEnum):
    DIRECT_VERIFIED = "direct_verified"        # OrderGuard itself connected, wrote a cart, read it back
    CONNECTOR_VERIFIED = "connector_verified"  # a real assistant connector produced a cart we checked
    AVAILABLE_UNTESTED = "available_untested"  # real and reachable in principle; not yet tried by us
    RESTRICTED = "restricted"                  # real, but access or policy blocks us specifically
    UNAVAILABLE = "unavailable"                # no public agent surface exists at all


class Capability(StrEnum):
    CART_MUTABLE = "cart_mutable"      # can build/return an itemized cart and total
    DISCOVERY_ONLY = "discovery_only"  # search/browse only; checkout happens elsewhere
    UNKNOWN = "unknown"                # no endpoint exists to evaluate a capability against


class ConnectorBackendType(StrEnum):
    """Added 2026-08-31, after a review correctly pointed out that "appears
    in Claude's consumer connector directory" and "our own backend can reach
    it independently" are two different claims, and this module was at risk
    of conflating them the moment agent-orchestrator routing needed a real
    answer to "can code in this repository actually call this thing."
    """

    REMOTE_MCP = "remote_mcp"                       # a real hosted MCP endpoint we (or a proven session) can speak to directly
    NATIVE_API_ADAPTER = "native_api_adapter"        # a direct vendor REST/GraphQL adapter, not MCP (e.g. Razorpay)
    CUSTOM_MCP = "custom_mcp"                        # user-added remote MCP URL, not yet built in this codebase
    CLAUDE_DIRECTORY_ONLY = "claude_directory_only"  # real and cart-capable inside claude.ai's own consumer app; not independently reachable by this backend
    BROWSER_HANDOFF = "browser_handoff"              # no cart API at all; checkout happens in the merchant's own app/site
    UNSUPPORTED = "unsupported"                      # no public agent surface exists


class Connector(NamedTuple):
    id: str
    label: str
    kind: str                 # "grocery" | "food" | "general" | "payments"
    evidence: Evidence
    capability: Capability    # UNKNOWN by convention for kind == "payments"
    protocol: str             # what it speaks, if anything
    evidence_note: str        # what we actually observed
    checked_on: str
    can_order: bool           # would OrderGuard place an order through it today
    note: str = ""
    endpoint: str = ""
    # Available to a person inside Claude, ChatGPT or VS Code today, even where
    # OrderGuard itself cannot connect. The two are genuinely different
    # questions and collapsing them into one produced a wrong entry once
    # already — see FAILURE_LOG F-012.
    in_assistant_directory: bool = False
    # Whether OUR OWN backend (not a person's Claude session) could route to
    # this connector today, independent of the evidence tier above. Defaults
    # to UNSUPPORTED — every entry below sets this explicitly rather than
    # inheriting a guess.
    backend_type: ConnectorBackendType = ConnectorBackendType.UNSUPPORTED


_CHECKED = "2026-08-28"
_CHECKED_TODAY = "2026-08-29"


# --- working today ---------------------------------------------------------

_SHOPIFY = tuple(
    Connector(
        id=store.domain,
        label=store.label,
        kind=store.kind,
        evidence=Evidence.DIRECT_VERIFIED,
        capability=Capability.CART_MUTABLE,
        protocol="Shopify Storefront MCP",
        evidence_note=(
            f"POST https://{store.domain}/api/mcp returned a catalogue with "
            "INR prices; cart written and read back independently"
        ),
        checked_on="2026-08-27",
        can_order=False,
        note="Guarded cart only. We build and verify the cart, then hand you the "
             "checkout link. We never complete a purchase on someone else's store.",
        backend_type=ConnectorBackendType.REMOTE_MCP,
    )
    for store in VERIFIED_STORES
)


# --- connected and proven live, via Claude Code's own MCP session ----------
# Not a probe from this codebase's own HTTP client — an actual authenticated
# session, added via `claude mcp add --transport http` and OAuth completed by
# the user in their own terminal (npm's mcp-remote path was Swiggy's
# documented Claude Desktop config; Claude Code has no such documented config,
# so this used Claude Code's own native remote-HTTP + OAuth support instead —
# the same underlying MCP+OAuth mechanism, an undocumented-for-this-client but
# working path). Full record_intent -> check_cart round trips run against
# REAL search results, through this repo's real running server, landing in
# the real tamper-evident audit chain (see docs/CONNECTORS.md).
_SWIGGY_LIVE = (
    Connector(
        id="swiggy-instamart",
        label="Swiggy Instamart",
        kind="grocery",
        evidence=Evidence.CONNECTOR_VERIFIED,
        capability=Capability.CART_MUTABLE,
        protocol="Swiggy MCP — Instamart",
        endpoint="https://mcp.swiggy.com/im",
        evidence_note=(
            "Live session, 2026-08-29: search_products('milk') returned real "
            "SKUs and real INR prices (e.g. Nandini Shubham Milk 500ml, "
            "productId 4D231F4M76, offer price 2700 paise). Fed into "
            "record_intent + check_cart: an honest 2-unit cart at the real "
            "price allowed 13/13; a tampered 20-unit cart at the same "
            "item_id was blocked on G_QUANTITIES_MATCH, G_PRICES_MATCH and "
            "G_WITHIN_CAP simultaneously — both calls landed in the real "
            "audit chain (seq 7-8)."
        ),
        checked_on=_CHECKED_TODAY,
        can_order=False,
        note="This app's own conversational search only reaches Shopify "
             "stores and FreshCart directly. Shop Instamart through your "
             "own connected Claude session instead, then hand the cart to "
             "check_cart to verify it before paying — proven live above.",
        backend_type=ConnectorBackendType.REMOTE_MCP,
    ),
    Connector(
        id="swiggy-food",
        label="Swiggy Food",
        kind="food",
        evidence=Evidence.CONNECTOR_VERIFIED,
        capability=Capability.CART_MUTABLE,
        protocol="Swiggy MCP — Food",
        endpoint="https://mcp.swiggy.com/food",
        evidence_note=(
            "Live session, 2026-08-29: search_restaurants('pizza') from a "
            "real saved Electronic City address returned 10 real open "
            "restaurants (Domino's, Pizza Hut, La Pino'z, etc.) with real "
            "prices and ratings. An honest 1-item cart (Domino's Pizza, "
            "₹400) recorded and checked: allow=true, 13/13."
        ),
        checked_on=_CHECKED_TODAY,
        can_order=False,
        note="This app's own conversational search only reaches Shopify "
             "stores and FreshCart directly. Shop Swiggy Food through your "
             "own connected Claude session instead, then hand the cart to "
             "check_cart to verify it before paying — proven live above.",
        backend_type=ConnectorBackendType.REMOTE_MCP,
    ),
    Connector(
        id="swiggy-dineout",
        label="Swiggy Dineout",
        kind="general",
        evidence=Evidence.CONNECTOR_VERIFIED,
        capability=Capability.DISCOVERY_ONLY,
        protocol="Swiggy MCP — Dineout",
        endpoint="https://mcp.swiggy.com/dineout",
        evidence_note=(
            "Live session, 2026-08-29: authentication genuinely works (no "
            "401, real saved-address lookup succeeded). search_restaurants_dineout "
            "returned an empty result across three different real queries "
            "(by saved address, by Electronic City coordinates, by central "
            "Bangalore coordinates) — recorded as what actually happened, "
            "not assumed to be user error. capability is DISCOVERY_ONLY "
            "until a real cart-shaped result (a bookable slot) is seen."
        ),
        checked_on=_CHECKED_TODAY,
        can_order=False,
        note="Connected and authenticated; the search itself did not return "
             "usable results in this session. Worth re-checking, not worth "
             "claiming CART_MUTABLE on the strength of authentication alone.",
        backend_type=ConnectorBackendType.REMOTE_MCP,
    ),
)


# --- real, but we cannot use them ------------------------------------------

_GATED = (
    Connector(
        id="zomato",
        label="Zomato",
        kind="food",
        evidence=Evidence.RESTRICTED,
        capability=Capability.CART_MUTABLE,
        protocol="Zomato MCP (OAuth 2.0, PKCE)",
        endpoint="https://mcp-server.zomato.com/mcp",
        in_assistant_directory=True,
        evidence_note=(
            "POST https://mcp-server.zomato.com/mcp -> HTTP 401. Both OAuth "
            "discovery documents return 200. Verified connector in Claude's "
            "directory. Refusal is not inferred from the README alone — a "
            "Zomato maintainer states it directly in their issue tracker: "
            "'We are not allowing any third party apps currently' (issue #35, "
            "Nov 2025) and 'We wont be allowing localhost currently due to "
            "impending security issues' (issue #33). 19 access requests filed "
            "between Oct 2025 and Jun 2026; 11 have no reply at all. 'Will "
            "enable the third party apps soon' was Oct 2025 (issue #9). "
            "Separately, and unrelated to whether OrderGuard itself can "
            "connect: a real Zomato cart, produced by a person's own "
            "authorized session, WAS fed into check_cart and correctly "
            "allowed/blocked (see docs/CONNECTORS.md) — that proves check_cart "
            "is connector-agnostic, not that OrderGuard can reach Zomato."
        ),
        checked_on=_CHECKED,
        can_order=False,
        note="You can add this to Claude yourself today and it will order food. "
             "OrderGuard cannot, for two independent reasons: localhost redirect "
             "URIs are explicitly refused, and third-party apps are not being "
             "registered. Ten months of an open queue says this is a policy, not "
             "a backlog. Excluded by their rules, not by our capability.",
        # The endpoint IS a real remote MCP server (mcp-server.zomato.com/mcp) —
        # backend_type describes what kind of surface it is, not whether policy
        # currently lets us reach it. RESTRICTED evidence already carries that.
        backend_type=ConnectorBackendType.REMOTE_MCP,
    ),
)


# --- real, in Claude's official directory, not yet tried by us -------------

_UNTESTED = (
    Connector(
        id="instacart",
        label="Instacart",
        kind="grocery",
        evidence=Evidence.AVAILABLE_UNTESTED,
        capability=Capability.CART_MUTABLE,
        protocol="Claude connector (official directory, April 2026)",
        endpoint="https://claude.com/connectors/instacart",
        in_assistant_directory=True,
        evidence_note=(
            "Instacart's own connector page describes finding items and "
            "adding them to a cart inside the conversation. Part of "
            "Anthropic's April 2026 consumer-connector batch. Not personally "
            "probed by us — no OrderGuard code has connected to it. The "
            "recommended next target for a second live check_cart proof, "
            "alongside Order by Cash App, because both genuinely build a "
            "cart rather than only browsing."
        ),
        checked_on=_CHECKED_TODAY,
        can_order=False,
        note="Real, official, one click to connect for a person — no access "
             "queue like Swiggy or Zomato. Still needs a live human session to "
             "actually produce the second check_cart proof; that step is not "
             "something this coding session can perform on its own.",
        # Real and cart-capable inside claude.ai's own consumer app; this
        # backend has never independently reached it — appearing in Claude's
        # directory is not the same claim as "our FastAPI app can call it."
        backend_type=ConnectorBackendType.CLAUDE_DIRECTORY_ONLY,
    ),
    Connector(
        id="cash-app-orders",
        label="Order by Cash App",
        kind="food",
        evidence=Evidence.AVAILABLE_UNTESTED,
        capability=Capability.CART_MUTABLE,
        protocol="Claude connector",
        endpoint="https://claude.com/connectors/cash-app",
        in_assistant_directory=True,
        evidence_note=(
            "Its own connector page: discover nearby restaurants, compare "
            "menus, 'build a cart and show me the checkout link' — entirely "
            "inside the conversation. Structurally identical in shape to the "
            "proven Zomato flow. Not personally probed by us."
        ),
        checked_on=_CHECKED_TODAY,
        can_order=False,
        note="Real, official, cart-capable. Untested by us for the same reason "
             "as Instacart: producing the proof needs a live human session in "
             "the real Claude app, not this coding session.",
        backend_type=ConnectorBackendType.CLAUDE_DIRECTORY_ONLY,
    ),
    Connector(
        id="uber-eats",
        label="Uber Eats",
        kind="food",
        evidence=Evidence.AVAILABLE_UNTESTED,
        capability=Capability.DISCOVERY_ONLY,
        protocol="Claude connector (official directory, April 2026)",
        endpoint="https://claude.com/connectors/uber-eats",
        in_assistant_directory=True,
        evidence_note=(
            "Uber's own help documentation is explicit: 'at this time you can "
            "only view restaurants and menus... you will finalize your cart "
            "and checkout in the Uber Eats app.' Claude never sees a cart to "
            "hand check_cart — checkout happens outside the conversation "
            "entirely. An earlier draft of this project's plan proposed Uber "
            "Eats as the second live-proof target; that was wrong, and this "
            "entry exists specifically to record the correction rather than "
            "silently fix it."
        ),
        checked_on=_CHECKED_TODAY,
        can_order=False,
        note="Good for showing a user what's available. Cannot be the second "
             "check_cart proof — there is no cart for us to check.",
        backend_type=ConnectorBackendType.BROWSER_HANDOFF,
    ),
)


# --- no public agent surface ------------------------------------------------

_ABSENT = tuple(
    Connector(
        id=cid, label=label, kind="grocery",
        evidence=Evidence.UNAVAILABLE,
        capability=Capability.UNKNOWN,
        protocol="none published",
        evidence_note="No official public agent API or MCP endpoint found.",
        checked_on=_CHECKED,
        can_order=False,
        note="Unofficial reverse-engineered servers circulate for some of these. "
             "We do not use them: they break the platform's terms and they spend "
             "real money.",
        backend_type=ConnectorBackendType.UNSUPPORTED,
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
        evidence=Evidence.DIRECT_VERIFIED,
        capability=Capability.UNKNOWN,   # not a shopping connector; cart-capability is N/A
        protocol="Razorpay REST API, test mode",
        evidence_note=(
            "Test order created, checkout completed, payment captured and then "
            "verified server-side: constant-time signature check, independent "
            "fetch, equality on status, amount, currency and order id."
        ),
        checked_on="2026-08-26",
        can_order=True,
        note="Test mode only. No live credentials, and no real money at any "
             "point in this project.",
        # A direct REST API (razorpay_client.py), not an MCP server — the
        # exact case NATIVE_API_ADAPTER exists for.
        backend_type=ConnectorBackendType.NATIVE_API_ADAPTER,
    ),
)


CONNECTORS: tuple[Connector, ...] = _SHOPIFY + _SWIGGY_LIVE + _GATED + _UNTESTED + _ABSENT + _PAYMENTS

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
        if c.evidence is Evidence.DIRECT_VERIFIED and c.kind != "payments"
        and (kind is None or c.kind == kind)
    )


def cart_capable_connectors(kind: str | None = None) -> tuple[Connector, ...]:
    """Everything that can hand back an itemized cart, regardless of whether
    WE have verified it yet — the pool recommend_connector() draws from."""
    return tuple(
        c for c in CONNECTORS
        if c.capability is Capability.CART_MUTABLE and c.kind != "payments"
        and (kind is None or c.kind == kind)
    )


def summary() -> dict[str, int]:
    counts = {evidence.value: 0 for evidence in Evidence}
    for connector in CONNECTORS:
        counts[connector.evidence.value] += 1
    return counts
