"""``AgentRuntime``: the interface both the Anthropic API runtime and the
Claude subscription (Agent SDK) runtime implement identically, so
``orchestrator.py`` never branches on which one is active. Both translate
from the same ``ConnectorInvocationSpec`` (see ``..tools``) into their own,
genuinely different wire formats — see ``api_runtime.py`` and
``subscription_runtime.py`` for exactly how they differ, verified directly
against each one's current docs rather than assumed to match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..tools import ConnectorInvocationSpec

__all__ = ["ToolCallEvent", "AgentTurnResult", "AgentRuntime", "ImageInput", "StubAgentRuntime"]


@dataclass(frozen=True)
class ImageInput:
    """One image attached to a user turn — an image-based shopping list,
    e.g. a photo of a handwritten grocery list. ``media_type`` is a real
    IANA image MIME type (``image/jpeg``, ``image/png``, ``image/webp``,
    ``image/gif`` — the four the Messages API documents); ``data_base64``
    is the raw base64-encoded image bytes, no data-URI prefix."""

    media_type: str
    data_base64: str


@dataclass
class ToolCallEvent:
    connector_id: str
    tool_name: str
    arguments: dict
    execution_id: str = ""
    server_name: str | None = None
    resource_ref: str | None = None
    result: object | None = None
    is_error: bool = False

    @property
    def succeeded(self) -> bool:
        """A tool request alone is never success; a result must survive."""
        return self.result is not None and not self.is_error


@dataclass
class AgentTurnResult:
    text: str
    tool_calls: list[ToolCallEvent]
    stop_reason: str
    runtime: str
    execution_id: str = ""
    usage: dict | None = None
    # Opaque continuation state for the NEXT call in the same conversation.
    # Each runtime writes and reads its own shape here — the subscription
    # runtime stores {"resume": "<sdk session id>"} (the Agent SDK's own
    # session resumption, verified present on the installed SDK:
    # ClaudeAgentOptions has a real `resume: str | None` field); the API
    # runtime stores {"history": [...]} (the Messages API is stateless, so
    # continuing a conversation means resending prior turns). Neither
    # runtime needs to understand the other's shape — the orchestrator only
    # ever passes back exactly what it was given.
    session_context: dict = field(default_factory=dict)
    # Real, verified evidence from the runtime's OWN connection layer that a
    # connector's MCP handshake failed THIS turn -- never a model claim.
    # Real, live-found gap (2026-09-04, see FAILURE_LOG.md F-044 addendum):
    # a model correctly reported "Swiggy Instamart's tools never loaded,
    # real connection failure" and this was dismissed as a hallucination
    # for multiple fix cycles, because `/api/agent/connectors`'s own
    # "CONNECTED" status only checks whether a token exists in our
    # database, not whether the connector's real MCP handshake is
    # currently succeeding -- the two silently drifted apart. The Claude
    # Agent SDK's own init message already reports per-server connection
    # status (`mcp_servers: [{"name": ..., "status": "failed"}]`); this
    # was captured in a diagnostic print but never surfaced as data before.
    failed_connector_ids: list[str] = field(default_factory=list)
    # Real, verified connection status for every connector the SDK actually
    # reported on this turn, keyed by connector_id, value whatever raw
    # status string it gave ("failed", "connected", or anything else it
    # ever reports) -- never a subset, never a model claim. See F-044's
    # fourth addendum: `/api/agent/connectors`'s own status page kept
    # showing "CONNECTED" from a stale token-existence check even while a
    # connector's real handshake was verifiably failing every turn; this is
    # what lets that page show real, currently-verified health instead.
    mcp_server_statuses: dict[str, str] = field(default_factory=dict)


class AgentRuntime(Protocol):
    name: str

    @property
    def configured(self) -> bool: ...

    async def run_turn(
        self,
        system: str,
        user: str,
        connectors: list[ConnectorInvocationSpec],
        session_context: dict | None = None,
        image: ImageInput | None = None,
    ) -> AgentTurnResult: ...


class StubAgentRuntime:
    """Offline, deterministic, scriptable. Every test in this project's
    suite runs without a real key or a real subscription token — this
    runtime is why the agent package is no exception. Also used to test that
    the R3 exclusion in ``..tools.allowed_tool_names`` fires *before* any
    wire-format tool list is built, by scripting connectors with an R3 tool
    and asserting ``FinancialToolExposureError`` regardless of what this
    stub would have returned.
    """

    name = "stub"

    def __init__(self, scripted: AgentTurnResult | None = None):
        self._scripted = scripted
        self.calls: list[tuple[str, str, list[ConnectorInvocationSpec]]] = []
        self.last_session_context: dict | None = None
        self.last_image: ImageInput | None = None

    @property
    def configured(self) -> bool:
        return True

    async def run_turn(self, system, user, connectors, session_context=None, image=None):
        # Exercise the same R3 check both real adapters must run, so a stub-
        # driven test can catch a registry mistake before it ever reaches a
        # real runtime.
        from ..tools import allowed_tool_names

        for spec in connectors:
            allowed_tool_names(spec)

        self.calls.append((system, user, list(connectors)))
        self.last_session_context = session_context
        self.last_image = image
        if self._scripted is not None:
            return self._scripted
        return AgentTurnResult(text="", tool_calls=[], stop_reason="end_turn", runtime=self.name)
