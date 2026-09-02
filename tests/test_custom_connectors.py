"""User-added remote MCP connectors: SSRF-checked at registration, tools
discovered disabled, and never enable-able at R3.
"""

import json
import socket

import httpx
import pytest

from orderguard.agent.custom_connectors import (
    CustomConnectorProtocolError, custom_connectors_engine, discover_tools,
    enable_tool, enabled_tools, register_custom_connector,
)
from orderguard.agent.ssrf_guard import SSRFRejected


@pytest.fixture
def engine():
    return custom_connectors_engine(":memory:")


@pytest.fixture(autouse=True)
def public_example_dns(monkeypatch):
    """Keep SSRF tests deterministic when the suite runs without DNS access."""
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_registering_a_private_ip_is_rejected(engine):
    with pytest.raises(SSRFRejected):
        register_custom_connector(engine, label="Evil", url="https://10.0.0.5/mcp")


def test_registering_a_real_https_url_succeeds(engine):
    row = register_custom_connector(engine, label="Example", url="https://example.com/mcp")
    assert row.id is not None
    assert row.label == "Example"


@pytest.mark.asyncio
async def test_discovered_tools_are_stored_disabled(engine):
    row = register_custom_connector(engine, label="Example", url="https://example.com/mcp")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"tools": [{"name": "search"}, {"name": "delete_everything"}]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    names = await discover_tools(engine, row.id, client=client)
    await client.aclose()

    assert set(names) == {"search", "delete_everything"}
    assert enabled_tools(engine, row.id) == ()  # nothing enabled just from discovery


@pytest.mark.asyncio
async def test_enabling_a_discovered_tool_makes_it_eligible(engine):
    row = register_custom_connector(engine, label="Example", url="https://example.com/mcp")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"tools": [{"name": "search"}]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await discover_tools(engine, row.id, client=client)
    await client.aclose()

    enable_tool(engine, row.id, "search", "R0", "FILES_READ")
    tools = enabled_tools(engine, row.id)
    assert len(tools) == 1
    assert tools[0].name == "search" and tools[0].risk_tier == "R0"


def test_a_tool_can_never_be_enabled_at_r3(engine):
    row = register_custom_connector(engine, label="Example", url="https://example.com/mcp")
    with pytest.raises(ValueError):
        enable_tool(engine, row.id, "pay", "R3", "PAYMENT_WRITE")


@pytest.mark.asyncio
async def test_malformed_tools_list_fails_closed_instead_of_returning_empty(engine):
    row = register_custom_connector(engine, label="Example", url="https://example.com/mcp")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"tools": [{"description": "missing name"}]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(CustomConnectorProtocolError):
        await discover_tools(engine, row.id, client=client)
    await client.aclose()
