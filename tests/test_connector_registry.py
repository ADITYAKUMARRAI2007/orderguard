"""The agent's connector registry: every entry classified, no R3 tool ever
listed. This is the regression test for the exact promise
``connector_registry.py``'s own docstring makes.
"""

from orderguard.agent.connector_registry import REGISTRY, by_id, tools_within_ceiling
from orderguard.connectors import ConnectorBackendType


def test_no_registered_connector_ever_lists_an_r3_tool():
    for connector in REGISTRY:
        for tool in connector.tools:
            assert tool.risk_tier != "R3", (
                f"{connector.id}/{tool.name} is R3 and must never be in the registry"
            )


def test_every_connector_has_a_real_backend_type():
    for connector in REGISTRY:
        assert isinstance(connector.backend_type, ConnectorBackendType)
        assert connector.backend_type != ConnectorBackendType.UNSUPPORTED


def test_swiggy_food_lists_get_addresses():
    """Regression: get_addresses is a real tool on Swiggy's Food MCP server
    too (its own description says "works for Swiggy Instamart and Food
    services"), but it was only ever registered for swiggy-instamart. A
    live multi-intent mission had the model correctly try to call it before
    a food order and the whole turn was vetoed as an ineligible tool
    selection — not because the model did anything wrong."""
    food = by_id("swiggy-food")
    assert "get_addresses" in {t.name for t in food.tools}


def test_github_is_the_required_non_commerce_proof():
    github = by_id("github")
    assert github.category == "DEV_TASK"
    assert all(t.risk_tier == "R0" for t in github.tools)


def test_tools_within_ceiling_excludes_r3_regardless_of_ceiling():
    from orderguard.agent.connector_registry import RegisteredConnector
    from orderguard.agent.tools import ToolPermission
    from orderguard.connectors import Capability, Evidence

    fake = RegisteredConnector(
        id="fake", label="Fake", category="TEST",
        backend_type=ConnectorBackendType.REMOTE_MCP, url="https://example.com/mcp",
        auth="none", evidence=Evidence.DIRECT_VERIFIED, capability=Capability.CART_MUTABLE,
        tools=(ToolPermission("read", "R0"), ToolPermission("pay", "R3")),
    )
    assert tools_within_ceiling(fake, "R3") == (ToolPermission("read", "R0"),)


def test_unknown_connector_id_raises():
    import pytest
    with pytest.raises(KeyError):
        by_id("does-not-exist")
