"""The R3 (financial) tool-exposure boundary: the one place in the agent
package capable of admitting a payment-capable tool into an LLM runtime's
tool list, and the one place that always refuses. Not a Python ``assert`` —
asserts compile out under ``-O`` and this boundary must hold even then.
"""

import pytest

from orderguard.agent.tools import (
    ConnectorInvocationSpec, FinancialToolExposureError,
    NonReadToolExposureError, ToolPermission,
    allowed_tool_names,
)


def test_r0_reads_pass_but_r1_is_fail_closed_during_read_only_staging():
    spec = ConnectorInvocationSpec(
        connector_id="swiggy-instamart",
        url="https://mcp.swiggy.com/im",
        tools=(ToolPermission("search_products", "R0"), ToolPermission("update_cart", "R1")),
    )
    with pytest.raises(NonReadToolExposureError) as excinfo:
        allowed_tool_names(spec)
    assert excinfo.value.tool_name == "update_cart"


def test_r0_read_tool_passes_through_untouched():
    spec = ConnectorInvocationSpec(
        connector_id="swiggy-instamart",
        url="https://mcp.swiggy.com/im",
        tools=(ToolPermission("search_products", "R0", "READ"),),
    )
    assert allowed_tool_names(spec) == ("search_products",)


def test_an_r3_tool_raises_before_any_wire_format_is_built():
    spec = ConnectorInvocationSpec(
        connector_id="swiggy-instamart",
        url="https://mcp.swiggy.com/im",
        tools=(ToolPermission("search_products", "R0"), ToolPermission("checkout", "R3")),
    )
    with pytest.raises(FinancialToolExposureError) as excinfo:
        allowed_tool_names(spec)
    assert excinfo.value.tool_name == "checkout"
    assert excinfo.value.connector_id == "swiggy-instamart"


def test_an_r3_tool_alone_also_raises():
    spec = ConnectorInvocationSpec(
        connector_id="x", url="https://example.com/mcp",
        tools=(ToolPermission("pay", "R3"),),
    )
    with pytest.raises(FinancialToolExposureError):
        allowed_tool_names(spec)
