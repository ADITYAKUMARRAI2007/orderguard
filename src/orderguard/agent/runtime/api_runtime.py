"""Anthropic Messages API runtime, using the MCP Connector beta.

Verified directly against platform.claude.com/docs/en/agents-and-tools/mcp-connector
before writing this (2026-08-30, re-confirmed 2026-08-31): ``mcp_servers`` is
a flat list of ``{"type": "url", "url": ..., "name": ..., "authorization_token":
...}`` entries; tool access is controlled via ``tools: [{"type": "mcp_toolset",
"mcp_server_name": ..., "configs": {tool: {"enabled": bool}}}]``; the call
needs ``betas=["mcp-client-2025-11-20"]``. This is a genuinely different wire
shape from the Agent SDK's (see ``subscription_runtime.py``) — both adapters
translate from ``ConnectorInvocationSpec``, never from each other.
"""

from __future__ import annotations

import uuid

import anthropic

from ..tools import (
    CliManagedConnectorUnsupported, ConnectorInvocationSpec,
    allowed_tool_names,
)
from .base import AgentTurnResult, ImageInput, ToolCallEvent

__all__ = ["AnthropicApiRuntimeUnavailable", "CliManagedConnectorUnsupported", "AnthropicApiRuntime"]

_DEFAULT_MODEL = "claude-sonnet-4-5"
_MCP_BETA = "mcp-client-2025-11-20"
_MAX_TOKENS = 2048


class AnthropicApiRuntimeUnavailable(RuntimeError):
    """No Anthropic API key configured — neither server-managed
    (``ANTHROPIC_API_KEY``) nor BYOK (``runtime_settings.py``)."""


class AnthropicApiRuntime:
    name = "api"

    def __init__(
        self,
        api_key: str | None,
        model: str = _DEFAULT_MODEL,
        client: anthropic.Anthropic | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._client = client  # injectable for tests; built lazily otherwise

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def run_turn(
        self, system: str, user: str, connectors: list[ConnectorInvocationSpec],
        session_context: dict | None = None, image: ImageInput | None = None,
    ) -> AgentTurnResult:
        if not self._api_key:
            raise AnthropicApiRuntimeUnavailable("no Anthropic API key configured")

        mcp_servers: list[dict] = []
        tools: list[dict] = []
        spec_by_server: dict[str, ConnectorInvocationSpec] = {}
        for spec in connectors:
            if spec.cli_managed:
                raise CliManagedConnectorUnsupported(
                    f"{spec.connector_id!r} is cli_managed; use an owner-scoped ConnectorAccount"
                )
            # Raises FinancialToolExposureError before any wire-format
            # config is built if an R3 tool ever reached this point.
            tool_names = allowed_tool_names(spec)
            server_name = spec.server_name or spec.connector_id
            spec_by_server[server_name] = spec
            server: dict = {"type": "url", "url": spec.url, "name": server_name}
            if spec.bearer_token:
                server["authorization_token"] = spec.bearer_token
            mcp_servers.append(server)
            tools.append({
                "type": "mcp_toolset",
                "mcp_server_name": server_name,
                "configs": {name: {"enabled": True} for name in tool_names},
            })

        # The Messages API is stateless — there is no server-side session to
        # resume, so a real continued conversation means resending prior
        # turns. `history` holds the plain-dict form of every prior
        # user/assistant message this conversation has had; a fresh
        # conversation passes an empty list.
        history: list[dict] = list((session_context or {}).get("history", []))
        # Image content blocks are a documented, native Messages API shape
        # (platform.claude.com/docs -> vision) -- a plain string `content`
        # stays the common case; an attached image switches to the block
        # list form, image first (the API's own documented ordering).
        user_content: str | list[dict] = user
        if image is not None:
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": image.data_base64,
                    },
                },
                {"type": "text", "text": user},
            ]
        messages = [*history, {"role": "user", "content": user_content}]

        client = self._client or anthropic.Anthropic(api_key=self._api_key)
        response = client.beta.messages.create(
            model=self._model,
            max_tokens=_MAX_TOKENS,
            betas=[_MCP_BETA],
            system=system,
            messages=messages,
            mcp_servers=mcp_servers,
            tools=tools,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCallEvent] = []
        calls_by_id: dict[str, ToolCallEvent] = {}
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "mcp_tool_use":
                server_name = getattr(block, "server_name", "") or ""
                spec = spec_by_server.get(server_name)
                call_id = getattr(block, "id", "") or uuid.uuid4().hex
                call = ToolCallEvent(
                    connector_id=spec.connector_id if spec else server_name,
                    tool_name=getattr(block, "name", "") or "",
                    arguments=getattr(block, "input", {}) or {},
                    execution_id=call_id,
                    server_name=server_name,
                    resource_ref=spec.resource_ref if spec else None,
                )
                tool_calls.append(call)
                calls_by_id[call_id] = call
            elif block_type == "mcp_tool_result":
                result_id = getattr(block, "tool_use_id", "") or ""
                call = calls_by_id.get(result_id)
                if call is None and len(tool_calls) == 1:
                    call = tool_calls[0]
                if call is not None:
                    call.result = getattr(block, "content", None)
                    call.is_error = bool(getattr(block, "is_error", False))

        for call in tool_calls:
            if call.result is None:
                call.is_error = True

        raw_usage = getattr(response, "usage", None)
        if hasattr(raw_usage, "model_dump"):
            usage = raw_usage.model_dump()
        elif isinstance(raw_usage, dict):
            usage = dict(raw_usage)
        else:
            usage = None

        # Echo the assistant's own content blocks back as history, in the
        # SDK's own dict shape (model_dump), rather than the raw response
        # objects — session_context must stay plain-dict/JSON-safe since it
        # travels through the API layer between calls.
        assistant_content = [
            block.model_dump() if hasattr(block, "model_dump") else block
            for block in response.content
        ]
        new_history = [*messages, {"role": "assistant", "content": assistant_content}]

        return AgentTurnResult(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=response.stop_reason or "",
            runtime=self.name,
            execution_id=getattr(response, "id", "") or uuid.uuid4().hex,
            usage=usage,
            session_context={"history": new_history},
        )
