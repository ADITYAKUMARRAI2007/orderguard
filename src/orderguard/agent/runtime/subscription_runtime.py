"""Claude subscription runtime, via the official Claude Agent SDK.

Verified directly against code.claude.com/docs/en/agent-sdk/mcp and the
installed ``claude_agent_sdk`` package's own signatures (2026-08-31): remote
servers are configured as ``mcp_servers={name: {"type": "http", "url": ...,
"headers": {...}}}``, tools allow-listed as ``"mcp__<server>__<tool>"``, and
— the finding that reshaped this whole plan's connector-auth design — the
SDK "doesn't open a browser or run an interactive OAuth flow... pass the
resulting access token in the server's headers." So this runtime cannot
silently inherit Swiggy's Claude-Code-session credential; it needs the same
backend-owned bearer token (``ConnectorAccount``) that the API runtime
needs. Authenticating *inference* (``CLAUDE_CODE_OAUTH_TOKEN``, from
``claude setup-token``, run once by the user in their own terminal) and
authenticating a *connector* are two separate concerns here, not one.

This runtime is intentionally the LOCAL_SINGLE_USER demo path: it sets
``CLAUDE_CODE_OAUTH_TOKEN`` on the process environment for the SDK to read,
which is a real limitation for any future multi-tenant deployment (the SDK
gives no per-call token parameter) — correctly out of scope for what this
build needs, and stated here rather than silently assumed away.

``cli_managed`` connector specs are explicitly refused. OrderGuard does not
read, inherit, or extract Claude Code's local connector credentials; the
same owner-scoped ConnectorAccount bearer token is used by both runtimes.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from ..tools import (
    CliManagedConnectorUnsupported, ConnectorInvocationSpec,
    allowed_tool_names,
)
from .base import AgentTurnResult, ImageInput, ToolCallEvent

__all__ = [
    "SubscriptionRuntimeUnavailable", "SubscriptionAuthFailed",
    "CliManagedConnectorUnsupported", "SubscriptionAgentRuntime",
]

_ALWAYS_DISALLOWED = ["Bash", "Read", "Write", "Edit", "Task", "WebSearch", "WebFetch"]

# A HOME scoped to this backend, not the operator's own. Real, reproduced
# incident: on a machine already logged into the `claude` CLI interactively
# (i.e. every machine this demo runs on — that login is how `claude
# setup-token` itself was generated), the subprocess spawned by the Agent SDK
# still finds that interactive session's own local state under the inherited
# HOME and every call 401s — even though the exact same
# CLAUDE_CODE_OAUTH_TOKEN authenticates correctly via a plain `claude -p`
# call. Verified directly: pointing the subprocess at an empty, isolated HOME
# (so it has no local session to conflict with) makes the identical token
# succeed, every time. This directory is scoped to the project rather than a
# throwaway temp dir so it isn't rebuilt on every call, and it is never
# seeded with a real login — nothing ever runs `claude login` inside it.
_SUBPROCESS_HOME = Path("data/subscription_runtime_home")


class SubscriptionRuntimeUnavailable(RuntimeError):
    """No CLAUDE_CODE_OAUTH_TOKEN configured — `claude setup-token` hasn't
    been run, or its output hasn't been added to `.env`."""


class SubscriptionAuthFailed(RuntimeError):
    """The configured CLAUDE_CODE_OAUTH_TOKEN was rejected (HTTP 401).

    Raised on the FIRST rejection rather than letting the SDK work through
    its ten-attempt retry ladder, which takes minutes and surfaces to the
    user as a request that simply never returns. A revoked or malformed
    token is not going to start working on attempt seven, so retrying is
    only a slower way to report the same thing.
    """


class SubscriptionAgentRuntime:
    name = "subscription"

    def __init__(self, oauth_token: str | None, model: str | None = None, query_fn=None):
        self._oauth_token = oauth_token
        self._model = model
        self._query_fn = query_fn or query  # injectable for tests

    @property
    def configured(self) -> bool:
        return bool(self._oauth_token)

    async def run_turn(
        self, system: str, user: str, connectors: list[ConnectorInvocationSpec],
        session_context: dict | None = None, image: ImageInput | None = None,
    ) -> AgentTurnResult:
        if not self._oauth_token:
            raise SubscriptionRuntimeUnavailable(
                "no CLAUDE_CODE_OAUTH_TOKEN configured (run `claude setup-token`)"
            )

        mcp_servers: dict[str, dict] = {}
        allowed_tools: list[str] = []
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
            server_cfg: dict = {"type": "http", "url": spec.url}
            if spec.bearer_token:
                server_cfg["headers"] = {"Authorization": f"Bearer {spec.bearer_token}"}
            mcp_servers[server_name] = server_cfg
            allowed_tools.extend(f"mcp__{server_name}__{t}" for t in tool_names)

        # Subscription auth and API-key auth are mutually exclusive, and the
        # CLI prefers ANTHROPIC_API_KEY when both are present. A stray or
        # wrong-service value in that variable therefore hijacks the
        # subscription token and returns 401 — which is exactly what happened
        # here: the host environment carried an unrelated `aero_live_…`
        # secret under ANTHROPIC_API_KEY, so every subscription run failed
        # auth while the same token worked fine via `claude -p`.
        #
        # Verified directly against the installed SDK's own transport
        # (_internal/transport/subprocess_cli.py): it builds the child
        # process's environment as
        #     {**inherited_os_environ, **options.env}
        # — an OVERLAY, not a replacement. Simply omitting a key from
        # `options.env` (an earlier version of this fix) does nothing: the
        # SDK's own `os.environ.items()` still supplies it underneath. The
        # only way to suppress an inherited variable is to overlay it with an
        # explicit falsy value, which is what this does.
        _SUBPROCESS_HOME.mkdir(parents=True, exist_ok=True)
        child_env = {
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_BASE_URL": "",
            "CLAUDE_CODE_OAUTH_TOKEN": self._oauth_token,
            # See _SUBPROCESS_HOME's docstring above: an isolated HOME is
            # what makes this token actually authenticate rather than 401
            # against the operator's own already-logged-in CLI session.
            "HOME": str(_SUBPROCESS_HOME.resolve()),
        }

        # Real conversation continuity, not a fresh, memoryless call every
        # time: the installed SDK has a genuine `resume: str | None` on
        # ClaudeAgentOptions (verified directly on the installed package,
        # not assumed). A None here is equivalent to omitting the argument
        # entirely — starts a fresh session, exactly like before this field
        # existed.
        resume_id = (session_context or {}).get("resume")

        options_kwargs: dict = dict(
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            disallowed_tools=_ALWAYS_DISALLOWED,
            system_prompt=system,
            model=self._model,
            env=child_env,
            resume=resume_id,
        )
        options = ClaudeAgentOptions(**options_kwargs)

        # The token now travels in `child_env` above rather than by mutating
        # this process's own os.environ, so concurrent requests can't clobber
        # each other's credential and the server's environment stays clean.

        text_parts: list[str] = []
        tool_calls: list[ToolCallEvent] = []
        calls_by_id: dict[str, ToolCallEvent] = {}
        stop_reason = "end_turn"
        usage: dict | None = None
        turn_execution_id = uuid.uuid4().hex
        new_session_id: str | None = None
        result_text: str | None = None
        failed_connector_ids: list[str] = []

        def capture_result(block: ToolResultBlock, fallback: object | None = None) -> None:
            call = calls_by_id.get(block.tool_use_id)
            if call is None:
                return
            call.result = block.content if block.content is not None else fallback
            call.is_error = bool(block.is_error)

        # `query()`'s own signature is `prompt: str | AsyncIterable[dict]` --
        # a plain string is the common case. An attached image switches to
        # the streaming dict form so the user turn carries a real Anthropic
        # content-block list (image + text) instead of only text; verified
        # live rather than assumed to work (see FAILURE_LOG.md for the
        # result of that check).
        prompt: str | object = user
        if image is not None:
            async def _image_prompt():
                yield {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": image.media_type,
                                    "data": image.data_base64,
                                },
                            },
                            {"type": "text", "text": user},
                        ],
                    },
                    "parent_tool_use_id": None,
                }
            prompt = _image_prompt()

        stream = self._query_fn(prompt=prompt, options=options)
        auth_failure = False
        async for message in stream:
            if isinstance(message, SystemMessage) and message.subtype == "init":
                # The SDK's own session id for this turn — captured every
                # time (not only when resuming) since a *fresh* call still
                # gets a real session_id, and that is exactly the value the
                # NEXT call must resume to continue this same conversation.
                init_data = getattr(message, "data", {}) or {}
                new_session_id = init_data.get("session_id")
                # Real, verified evidence the SDK's own connection layer
                # reports here -- never a model claim. Real, live-found gap
                # (2026-09-04, see FAILURE_LOG.md F-044 addendum): a model's
                # report that Swiggy Instamart's tools never loaded due to a
                # real connection failure was dismissed as a hallucination
                # for multiple fix cycles, because this init message was
                # never read past `session_id` before -- it was reporting
                # exactly this the whole time.
                for server_status in init_data.get("mcp_servers", []):
                    if server_status.get("status") == "failed":
                        spec = spec_by_server.get(server_status.get("name", ""))
                        connector_id = spec.connector_id if spec else server_status.get("name")
                        if connector_id and connector_id not in failed_connector_ids:
                            failed_connector_ids.append(connector_id)
                            print(
                                f"[agent] MCP handshake failed for {connector_id!r} "
                                f"(server_name={server_status.get('name')!r})",
                                file=sys.stderr,
                            )
            if isinstance(message, SystemMessage) and message.subtype == "api_retry":
                data = getattr(message, "data", {}) or {}
                if data.get("error_status") == 401 or data.get("error") == "authentication_failed":
                    # Stop at the first permanent auth failure. Breaking out
                    # lets the SDK generator reach a suspended yield point;
                    # explicitly closing it there avoids an event-loop-shutdown
                    # race ("aclose(): asynchronous generator is already
                    # running") observed in a real rejected-token run.
                    auth_failure = True
                    break
            if isinstance(message, AssistantMessage):
                if message.usage:
                    usage = dict(message.usage)
                if message.stop_reason:
                    stop_reason = message.stop_reason
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name.startswith("mcp__"):
                        _, server_name, tool_name = block.name.split("__", 2)
                        spec = spec_by_server.get(server_name)
                        connector_id = spec.connector_id if spec else server_name
                        call = ToolCallEvent(
                            connector_id=connector_id,
                            tool_name=tool_name,
                            arguments=block.input or {},
                            execution_id=block.id,
                            server_name=server_name,
                            resource_ref=spec.resource_ref if spec else None,
                        )
                        tool_calls.append(call)
                        calls_by_id[block.id] = call
                    elif isinstance(block, ToolResultBlock):
                        capture_result(block)
                    elif isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                result_blocks = [b for b in message.content if isinstance(b, ToolResultBlock)]
                for block in result_blocks:
                    capture_result(block, message.tool_use_result)
            elif isinstance(message, ResultMessage):
                stop_reason = message.subtype or "end_turn"
                if message.usage:
                    usage = dict(message.usage)
                # ResultMessage.result restates the SAME final text the
                # AssistantMessage TextBlocks already delivered — appending
                # it unconditionally duplicated every real response
                # (observed live: a real "OK" turn produced text=="OKOK", and
                # a real address-list turn repeated the whole message twice
                # in the console). Used only as a fallback when no TextBlock
                # arrived at all.
                result_text = message.result

        if auth_failure:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
            raise SubscriptionAuthFailed(
                "CLAUDE_CODE_OAUTH_TOKEN was rejected (HTTP 401). Generate a fresh "
                "one with `claude setup-token` and set it as a single line in .env."
            )

        # A ToolUseBlock proves only that Claude requested a call. If the SDK
        # never delivered its matching ToolResultBlock, the call is failed.
        for call in tool_calls:
            if call.result is None:
                call.is_error = True

        text = "".join(text_parts) or (result_text or "")

        return AgentTurnResult(
            text=text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            runtime=self.name,
            execution_id=turn_execution_id,
            usage=usage,
            session_context={"resume": new_session_id} if new_session_id else {},
            failed_connector_ids=failed_connector_ids,
        )
