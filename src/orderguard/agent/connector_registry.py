"""Capability-first registry of connectors the agent orchestrator may route
to. Reuses ``connectors.py``'s ``Evidence``/``Capability``/``ConnectorBackendType``
vocabulary rather than inventing a second one — a review of an earlier draft
correctly flagged that two divergent definitions of "can we shop this" would
be worse than one, even an imperfect one.

Every tool listed here carries an explicit risk tier, and no R3 (financial)
tool is ever listed. That is a promise about this file, not just about the
runtime code that reads it — ``tests/test_connector_registry.py`` asserts it
directly, and ``agent/tools.py``'s hard invariant is what happens if this
promise is ever broken anyway.

GitHub is here, not in ``connectors.py``: it is not a commerce surface, and
that module's docstring is explicit that it catalogues "every agent-commerce
surface we know of." GitHub exists here specifically as the required
non-commerce proof — chosen because it needs only a personal access token,
not a full OAuth app registration, which is the fastest real (not stubbed)
path to a second, genuinely different connector executing end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..connectors import Capability, ConnectorBackendType, Evidence
from .tools import ToolPermission

__all__ = ["RegisteredConnector", "REGISTRY", "by_id", "tools_within_ceiling"]

_RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


@dataclass(frozen=True)
class RegisteredConnector:
    id: str
    label: str
    category: str              # e.g. "COMMERCE_GROCERY", "COMMERCE_FOOD", "DEV_TASK"
    backend_type: ConnectorBackendType
    url: str                   # "" when resolved per-call (e.g. per-Shopify-store)
    auth: str                  # "none" | "connector_account"
    evidence: Evidence
    capability: Capability
    tools: tuple[ToolPermission, ...]
    note: str = ""
    provider: str = ""
    vertical: str = ""
    transport: str = "streamable_http"
    capabilities: tuple[str, ...] = ()
    runtime_compatibility: tuple[str, ...] = ("subscription", "api", "stub")
    regions: tuple[str, ...] = ("GLOBAL",)
    health: str = "HEALTHY"
    available: bool = True
    last_verified_at: str | None = None
    # A real page on the connector's own site — never a guessed deep-link to
    # a cart/checkout path we have not confirmed exists. Once OrderGuard
    # writes to a real cart via this connector's own MCP tool (never through
    # Razorpay, which only settles for OUR merchant), the user finishes
    # there themselves.
    checkout_url: str = ""


REGISTRY: tuple[RegisteredConnector, ...] = (
    RegisteredConnector(
        id="swiggy-instamart",
        label="Swiggy Instamart",
        category="COMMERCE_GROCERY",
        backend_type=ConnectorBackendType.REMOTE_MCP,
        url="https://mcp.swiggy.com/im",
        auth="connector_account",
        evidence=Evidence.CONNECTOR_VERIFIED,
        capability=Capability.CART_MUTABLE,
        tools=(
            ToolPermission("search_products", "R0", "READ"),
            ToolPermission("get_cart", "R0", "READ"),
            ToolPermission("get_addresses", "R0", "READ"),
            ToolPermission("update_cart", "R1", "REVERSIBLE_WRITE"),
            # checkout / confirm_order / place_food_order are real Swiggy
            # tools and are deliberately absent here: they are R3 (financial)
            # and must never be offered to an LLM runtime. See agent/tools.py.
        ),
        note="Live-proven via a Claude Code session (docs/CONNECTORS.md). "
             "Backend OAuth via ConnectorAccount is what this build adds — "
             "see connector_accounts.py.",
        provider="Swiggy", vertical="grocery",
        capabilities=("COMMERCE_GROCERY",), regions=("IN",),
        last_verified_at="2026-08-29T00:00:00Z",
        # Real, confirmed path (2026-09-04, a real user's own browser
        # screenshot): the generic /instamart homepage forces Swiggy to
        # re-detect a delivery location from the browser's own signal
        # (IP/geolocation), which is not the address a real write just used
        # -- exactly the "sorry, we do not deliver here" a real user hit.
        # /instamart/cart on swiggy.com was the fix for THAT, at the time.
        #
        # Superseded (2026-09-06, another real user's own side-by-side
        # screenshots, same account, same moment): swiggy.com/instamart/cart
        # now renders "Your cart is getting lonely" -- genuinely empty --
        # while instamart.in/cart, opened at the exact same time, shows the
        # real populated cart (matching this backend's own independent
        # get_cart read: same two items, same ₹ total). Swiggy has split
        # Instamart onto its own domain since the 09-04 fix; swiggy.com's
        # path is a stale, separately-backed cart view now, not a redirect.
        checkout_url="https://www.instamart.in/cart",
    ),
    RegisteredConnector(
        id="swiggy-food",
        label="Swiggy Food",
        category="COMMERCE_FOOD",
        backend_type=ConnectorBackendType.REMOTE_MCP,
        url="https://mcp.swiggy.com/food",
        auth="connector_account",
        evidence=Evidence.CONNECTOR_VERIFIED,
        capability=Capability.CART_MUTABLE,
        tools=(
            ToolPermission("search_restaurants", "R0", "READ"),
            ToolPermission("search_menu", "R0", "READ"),
            ToolPermission("get_food_cart", "R0", "READ"),
            # Verified live against the real Swiggy Food MCP tool schema
            # (its own description: "This tool works for Swiggy Instamart
            # and Food services") — a real, reproduced incident: a live
            # multi-intent mission had the model correctly try to call this
            # before ordering food (same reason Instamart requires it), and
            # the whole turn was vetoed because it was missing here, not
            # because the model did anything wrong.
            ToolPermission("get_addresses", "R0", "READ"),
            ToolPermission("update_food_cart", "R1", "REVERSIBLE_WRITE"),
        ),
        note="Same backend-OAuth build target as Instamart.",
        provider="Swiggy", vertical="food",
        capabilities=("COMMERCE_FOOD",), regions=("IN",),
        last_verified_at="2026-08-29T00:00:00Z",
    ),
    RegisteredConnector(
        id="shopify",
        label="Shopify (verified stores)",
        category="COMMERCE_GENERAL",
        backend_type=ConnectorBackendType.REMOTE_MCP,
        url="",  # resolved per-store from commerce.stores at routing time
        auth="none",
        evidence=Evidence.DIRECT_VERIFIED,
        capability=Capability.CART_MUTABLE,
        tools=(
            ToolPermission("search_catalog", "R0", "READ"),
            ToolPermission("get_cart", "R0", "READ"),
            ToolPermission("update_cart", "R1", "REVERSIBLE_WRITE"),
        ),
        note="No key needed — see commerce/shopify_mcp.py. URL resolved "
             "per verified store, not fixed.",
        provider="Shopify", vertical="commerce",
        capabilities=("COMMERCE_GENERAL",), regions=("GLOBAL", "IN"),
        last_verified_at="2026-08-31T00:00:00Z",
    ),
    RegisteredConnector(
        id="github",
        label="GitHub",
        category="DEV_TASK",
        backend_type=ConnectorBackendType.REMOTE_MCP,
        url="https://api.githubcopilot.com/mcp/",
        auth="connector_account",
        evidence=Evidence.AVAILABLE_UNTESTED,
        capability=Capability.DISCOVERY_ONLY,
        tools=(
            ToolPermission("list_issues", "R0", "READ"),
        ),
        note="Official, hosted remote MCP server (Anthropic's own documented "
             "example). Chosen as the required non-commerce proof because "
             "auth is a personal access token, not a full OAuth app — the "
             "fastest real path to a second, genuinely different connector "
             "executing end-to-end, not a stub.",
        provider="GitHub", vertical="development",
        capabilities=("DEV_TASK",), regions=("GLOBAL",),
    ),
)

_BY_ID = {c.id: c for c in REGISTRY}


def by_id(connector_id: str) -> RegisteredConnector:
    try:
        return _BY_ID[connector_id]
    except KeyError:
        raise KeyError(f"unknown registered connector: {connector_id!r}") from None


def tools_within_ceiling(
    connector: RegisteredConnector, max_risk_tier: str = "R0"
) -> tuple[ToolPermission, ...]:
    """Return the currently exposable R0 read tools.

    ``max_risk_tier`` is retained for API compatibility and may narrow the
    set, but cannot widen it beyond R0 while mutation execution is not wired.
    R1/R2 tools remain visible in the registry as disabled control-plane
    metadata; R3 is never returned under any setting.
    """
    requested = _RISK_ORDER[max_risk_tier]
    ceiling = min(requested, _RISK_ORDER["R0"])
    return tuple(
        t for t in connector.tools
        if _RISK_ORDER[t.risk_tier] <= ceiling
        and t.risk_tier == "R0"
        and t.mutation_classification == "READ"
    )
