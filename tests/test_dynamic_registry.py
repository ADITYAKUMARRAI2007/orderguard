"""Auto-registering every MCP server from the user's Claude subscription.

The invariant that matters most here: auto-discovery must never smuggle an
unclassified tool into a model's tool list. A newly-detected server could
expose `checkout` or `book_ride`; until its tools are listed and given a
risk tier, it must offer nothing.
"""

from orderguard.agent.claude_code_detect import DetectedConnector
from orderguard.agent.connector_registry import REGISTRY as STATIC_REGISTRY
from orderguard.agent.dynamic_registry import auto_registered, category_for, merged_registry
from orderguard.connectors import ConnectorBackendType, Evidence


def _d(name, url="https://example.com/mcp", connected=True, cli=True):
    return DetectedConnector(
        name=name, url=url, connected=connected, status_text="Connected", cli_managed=cli
    )


def test_categories_are_inferred_for_real_connected_servers():
    assert category_for("swiggy-instamart") == "COMMERCE_GROCERY"
    assert category_for("swiggy-food") == "COMMERCE_FOOD"
    assert category_for("swiggy-dineout") == "DINING_RESERVATION"
    assert category_for("claude.ai Uber") == "TRANSPORT_RIDE"
    assert category_for("claude.ai Booking.com") == "TRAVEL_ACCOMMODATION"
    assert category_for("claude.ai Kiwi.com") == "TRAVEL_FLIGHT"
    assert category_for("claude.ai Razorpay") == "PAYMENTS"
    assert category_for("claude.ai Instacart") == "COMMERCE_GROCERY"
    assert category_for("claude.ai Zomato") == "COMMERCE_FOOD"
    assert category_for("claude.ai Morningstar") == "FINANCE_RESEARCH"


def test_an_unrecognised_server_is_unclassified_not_guessed_into_commerce():
    assert category_for("some-internal-tool-xyz") == "UNCLASSIFIED"


def test_auto_registered_connectors_expose_no_tools():
    """The core safety property: an auto-detected server offers the model
    nothing until a human classifies its tools."""
    entries = auto_registered([_d("claude.ai Uber"), _d("claude.ai Instacart")], frozenset())
    assert entries
    for entry in entries:
        assert entry.tools == ()


def test_curated_entries_are_never_shadowed_by_a_toolless_auto_entry():
    known = frozenset(c.id for c in STATIC_REGISTRY)
    entries = auto_registered([_d("swiggy-instamart"), _d("github")], known)
    assert entries == []


def test_cli_managed_is_remote_mcp_but_claude_directory_is_not():
    cli, directory = auto_registered(
        [_d("swiggy-dineout", cli=True), _d("claude.ai Zomato", cli=False)], frozenset()
    )
    assert cli.backend_type == ConnectorBackendType.REMOTE_MCP
    assert directory.backend_type == ConnectorBackendType.CLAUDE_DIRECTORY_ONLY


def test_auto_entries_are_available_untested_never_verified():
    entry = auto_registered([_d("claude.ai Kiwi.com")], frozenset())[0]
    assert entry.evidence is Evidence.AVAILABLE_UNTESTED


def test_merged_registry_keeps_every_curated_entry_and_adds_the_rest():
    detected = [_d("swiggy-instamart"), _d("claude.ai Uber"), _d("claude.ai Zomato", cli=False)]
    merged = merged_registry(detected)
    ids = [c.id for c in merged]
    for curated in STATIC_REGISTRY:
        assert curated.id in ids
    assert "claude.ai Uber" in ids
    assert "claude.ai Zomato" in ids


def test_labels_drop_the_claude_ai_prefix():
    entry = auto_registered([_d("claude.ai Booking.com", cli=False)], frozenset())[0]
    assert entry.label == "Booking.com"
