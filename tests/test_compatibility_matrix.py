"""Every connector in ``connectors.py`` must carry a real ``backend_type`` —
the fix for an earlier draft's conflation of "exists in Claude's consumer
directory" with "our own backend can reach it independently."
"""

from orderguard.connectors import CONNECTORS, ConnectorBackendType, Evidence


def test_every_connector_has_a_classified_backend_type():
    for connector in CONNECTORS:
        assert isinstance(connector.backend_type, ConnectorBackendType)


def test_available_untested_connectors_are_never_silently_claimed_reachable():
    """AVAILABLE_UNTESTED must never be paired with REMOTE_MCP unless a real
    endpoint was actually probed — CLAUDE_DIRECTORY_ONLY is the honest tier
    for "real inside Claude's own app, never independently reached by us."
    """
    for connector in CONNECTORS:
        if connector.evidence is Evidence.AVAILABLE_UNTESTED and connector.in_assistant_directory:
            assert connector.backend_type in (
                ConnectorBackendType.CLAUDE_DIRECTORY_ONLY,
                ConnectorBackendType.BROWSER_HANDOFF,
            ), (
                f"{connector.id} is AVAILABLE_UNTESTED and only known via "
                "Claude's directory — it must not claim REMOTE_MCP without a "
                "real independent probe"
            )


def test_verified_commerce_connectors_are_remote_mcp():
    for connector in CONNECTORS:
        if connector.evidence in (Evidence.DIRECT_VERIFIED, Evidence.CONNECTOR_VERIFIED) and connector.kind != "payments":
            assert connector.backend_type == ConnectorBackendType.REMOTE_MCP


def test_razorpay_is_a_native_api_adapter_not_mcp():
    razorpay = next(c for c in CONNECTORS if c.id == "razorpay")
    assert razorpay.backend_type == ConnectorBackendType.NATIVE_API_ADAPTER
