"""Every MCP server connected to the user's own Claude subscription, merged
into the agent's routable catalog automatically.

`connector_registry.REGISTRY` is the hand-curated set whose tools we have
actually classified by risk. But a user's Claude account may have many more
servers connected (`claude mcp list`), and they should show up and become
routable without anyone editing Python.

**The safety line, and why it is drawn here.** Auto-discovering a server is
not the same as trusting its tools. We cannot know whether a newly-detected
server exposes `book_ride`, `checkout` or `place_order` until we have
actually listed and classified its tools. So an auto-registered connector is
admitted to the catalog with an EMPTY tool tuple: it is visible, it is
categorised, its auth state is real — but the orchestrator can offer the
model nothing from it until its tools have been discovered and given a risk
tier. That keeps the R3 invariant (`agent/tools.py`) true by construction
rather than by hoping an unknown server has no payment tool.
"""

from __future__ import annotations

import re

from ..connectors import Capability, ConnectorBackendType, Evidence
from .claude_code_detect import DetectedConnector, detect_claude_code_connectors
from .connector_registry import REGISTRY as STATIC_REGISTRY, RegisteredConnector

__all__ = ["category_for", "auto_registered", "merged_registry"]

# Name -> capability category. Ordered: first match wins, so more specific
# patterns precede general ones.
_CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"instamart|instacart|grocer|bigbasket|blinkit|zepto", "COMMERCE_GROCERY"),
    (r"dineout|opentable|resy", "DINING_RESERVATION"),
    (r"swiggy-food|zomato|doordash|ubereats|uber-eats|deliveroo|food", "COMMERCE_FOOD"),
    (r"booking\.com|airbnb|hotel|expedia|agoda", "TRAVEL_ACCOMMODATION"),
    (r"kiwi|flight|skyscanner|airline", "TRAVEL_FLIGHT"),
    (r"\buber\b|lyft|ola|cab|ride", "TRANSPORT_RIDE"),
    (r"razorpay|stripe|paypal|adyen", "PAYMENTS"),
    (r"morningstar|bloomberg|finance|market", "FINANCE_RESEARCH"),
    (r"shopify|amazon|ebay|etsy|retail|commerce", "COMMERCE_GENERAL"),
    (r"github|gitlab|linear|jira", "DEV_TASK"),
    (r"gmail|outlook|mail", "COMMUNICATION_EMAIL"),
    (r"calendar", "PRODUCTIVITY_CALENDAR"),
    (r"notion|drive|dropbox|docs", "PRODUCTIVITY_DOCS"),
    (r"slack|discord|teams", "COMMUNICATION_CHAT"),
    (r"spotify|youtube|netflix", "ENTERTAINMENT"),
)


def category_for(name: str) -> str:
    """Infer a capability category from a server name. Falls back to
    UNCLASSIFIED rather than guessing a commerce category — a wrong guess
    would put a server into a routing pool it does not belong in."""
    lowered = name.lower()
    for pattern, category in _CATEGORY_PATTERNS:
        if re.search(pattern, lowered):
            return category
    return "UNCLASSIFIED"


def _label_for(detected: DetectedConnector) -> str:
    # `claude mcp list` prefixes consumer-directory entries with "claude.ai ".
    return detected.name.removeprefix("claude.ai ").strip()


def auto_registered(
    detected: list[DetectedConnector], known_ids: frozenset[str]
) -> list[RegisteredConnector]:
    """Turn detected CLI/claude.ai servers into registry entries, skipping
    any id the curated registry already defines (those have real, classified
    tools and must not be shadowed by a tool-less auto entry)."""
    out: list[RegisteredConnector] = []
    for d in detected:
        if d.name in known_ids:
            continue
        out.append(
            RegisteredConnector(
                id=d.name,
                label=_label_for(d),
                category=category_for(d.name),
                # A CLI-managed server is a real remote MCP endpoint this
                # machine can reach. A `claude.ai `-prefixed one lives inside
                # Claude's consumer app; this backend has never independently
                # reached it, which connectors.py already has a name for.
                backend_type=(
                    ConnectorBackendType.REMOTE_MCP
                    if d.cli_managed
                    else ConnectorBackendType.CLAUDE_DIRECTORY_ONLY
                ),
                url=d.url,
                auth="connector_account",
                evidence=Evidence.AVAILABLE_UNTESTED,
                capability=Capability.UNKNOWN,
                # Empty on purpose — see module docstring. Nothing from an
                # unclassified server is ever offered to a model.
                tools=(),
                note=(
                    f"Auto-detected from your Claude session ({d.status_text}). "
                    "Tools are not yet discovered or risk-classified, so nothing "
                    "from this server can be offered to the model yet."
                ),
            )
        )
    return out


def merged_registry(detected: list[DetectedConnector] | None = None) -> list[RegisteredConnector]:
    """The curated registry plus everything detected on this machine."""
    if detected is None:
        detected, _error = detect_claude_code_connectors()
    known = frozenset(c.id for c in STATIC_REGISTRY)
    return list(STATIC_REGISTRY) + auto_registered(detected, known)
