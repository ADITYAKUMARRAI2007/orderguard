"""Both runtimes translate the SAME ``ConnectorInvocationSpec`` into their
own, genuinely different wire formats. This is the regression test for the
exact mistake an earlier draft of this plan made — claiming the two
runtimes shared one config shape, which a direct doc check disproved.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from claude_agent_sdk import AssistantMessage, SystemMessage, ToolResultBlock, ToolUseBlock, UserMessage

from orderguard.agent.runtime.api_runtime import AnthropicApiRuntime, AnthropicApiRuntimeUnavailable
from orderguard.agent.runtime.subscription_runtime import (
    SubscriptionAgentRuntime, SubscriptionRuntimeUnavailable,
)
from orderguard.agent.tools import ConnectorInvocationSpec, FinancialToolExposureError, ToolPermission


def _spec(*tools):
    return ConnectorInvocationSpec(
        connector_id="github", url="https://api.githubcopilot.com/mcp/",
        tools=tools, bearer_token="ghp_xxx",
    )


# --- AnthropicApiRuntime -----------------------------------------------------

def test_api_runtime_unconfigured_without_a_key():
    runtime = AnthropicApiRuntime(api_key=None)
    assert not runtime.configured
    with pytest.raises(AnthropicApiRuntimeUnavailable):
        import asyncio
        asyncio.run(runtime.run_turn("sys", "user", []))


def test_api_runtime_builds_mcp_toolset_wire_shape():
    fake_client = MagicMock()
    fake_client.beta.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn",
    )
    runtime = AnthropicApiRuntime(api_key="sk-ant-test", client=fake_client)

    import asyncio
    result = asyncio.run(runtime.run_turn("sys", "user", [_spec(ToolPermission("list_issues", "R0"))]))

    _, kwargs = fake_client.beta.messages.create.call_args
    assert kwargs["betas"] == ["mcp-client-2025-11-20"]
    assert kwargs["mcp_servers"] == [{
        "type": "url", "url": "https://api.githubcopilot.com/mcp/",
        "name": "github", "authorization_token": "ghp_xxx",
    }]
    assert kwargs["tools"] == [{
        "type": "mcp_toolset", "mcp_server_name": "github",
        "configs": {"list_issues": {"enabled": True}},
    }]
    assert result.text == "done"
    assert result.runtime == "api"


def test_api_runtime_refuses_an_r3_tool_before_calling_anthropic_at_all():
    fake_client = MagicMock()
    runtime = AnthropicApiRuntime(api_key="sk-ant-test", client=fake_client)

    import asyncio
    with pytest.raises(FinancialToolExposureError):
        asyncio.run(runtime.run_turn("sys", "user", [_spec(ToolPermission("checkout", "R3"))]))
    fake_client.beta.messages.create.assert_not_called()


def test_api_runtime_captures_tool_use_and_result_blocks():
    fake_client = MagicMock()
    fake_client.beta.messages.create.return_value = SimpleNamespace(
        content=[
            SimpleNamespace(type="mcp_tool_use", server_name="github", name="list_issues", input={"repo": "x/y"}),
            SimpleNamespace(type="mcp_tool_result", content=[{"number": 1}], is_error=False),
        ],
        stop_reason="end_turn",
    )
    runtime = AnthropicApiRuntime(api_key="sk-ant-test", client=fake_client)
    import asyncio
    result = asyncio.run(runtime.run_turn("sys", "user", [_spec(ToolPermission("list_issues", "R0"))]))
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "list_issues"
    assert result.tool_calls[0].result == [{"number": 1}]


# --- SubscriptionAgentRuntime -------------------------------------------------

def test_subscription_runtime_unconfigured_without_a_token():
    runtime = SubscriptionAgentRuntime(oauth_token=None)
    assert not runtime.configured
    with pytest.raises(SubscriptionRuntimeUnavailable):
        import asyncio
        asyncio.run(runtime.run_turn("sys", "user", []))


def test_subscription_runtime_builds_agent_sdk_wire_shape():
    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        captured["prompt"] = prompt
        return
        yield  # pragma: no cover - makes this an async generator

    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    import asyncio
    asyncio.run(runtime.run_turn("sys", "user", [_spec(ToolPermission("list_issues", "R0"))]))

    options = captured["options"]
    assert options.mcp_servers == {
        "github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/",
                   "headers": {"Authorization": "Bearer ghp_xxx"}},
    }
    assert options.allowed_tools == ["mcp__github__list_issues"]


def test_subscription_runtime_refuses_an_r3_tool_before_calling_the_sdk_at_all():
    called = {"yes": False}

    async def fake_query(*, prompt, options):
        called["yes"] = True
        return
        yield  # pragma: no cover

    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    import asyncio
    with pytest.raises(FinancialToolExposureError):
        asyncio.run(runtime.run_turn("sys", "user", [_spec(ToolPermission("checkout", "R3"))]))
    assert called["yes"] is False


def test_subscription_runtime_preserves_actual_mcp_tool_result_for_normalizer():
    raw_result = [{
        "type": "text",
        "text": '{"issues":[{"number":42,"title":"Fix bug","state":"open","html_url":"https://github.com/x/y/issues/42","user":{"login":"octocat"}}]}',
    }]

    async def fake_query(*, prompt, options):
        yield AssistantMessage(
            content=[ToolUseBlock(
                id="toolu_42", name="mcp__github__list_issues",
                input={"owner": "x", "repo": "y"},
            )],
            model="claude-test",
        )
        yield UserMessage(content=[ToolResultBlock(
            tool_use_id="toolu_42", content=raw_result, is_error=False,
        )])

    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    import asyncio
    turn = asyncio.run(runtime.run_turn(
        "sys", "user", [_spec(ToolPermission("list_issues", "R0"))]
    ))

    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call.execution_id == "toolu_42"
    assert call.result is raw_result
    assert call.succeeded is True

    from orderguard.agent.normalizer import normalize
    normalized = normalize(
        call, capability="DEV_TASK", risk_tier="R0",
        provenance="subscription:github",
    )
    assert normalized.payload.items[0]["number"] == 42


def test_subscription_runtime_reports_a_real_mcp_handshake_failure():
    """Real, live-found gap (2026-09-04, see FAILURE_LOG.md F-044's
    addendum): the SDK's own init message reports per-server MCP
    connection status (`mcp_servers: [{"name": ..., "status": "failed"}]`)
    -- this codebase read `session_id` off that same message and discarded
    everything else, so a genuine connection failure looked identical to
    the model just not bothering to call a working tool. Model claims
    about this were dismissed as hallucinations for multiple fix cycles
    as a direct result."""
    async def fake_query(*, prompt, options):
        yield SystemMessage(subtype="init", data={
            "session_id": "sdk-session-1",
            "mcp_servers": [{"name": "swiggy-instamart", "status": "failed"}],
        })
        return
        yield  # pragma: no cover

    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    import asyncio
    turn = asyncio.run(runtime.run_turn(
        "sys", "user", [_spec(ToolPermission("search_products", "R0"))],
    ))
    # Real evidence surfaces even though the one spec offered this turn was
    # "github" (from the shared `_spec` helper) -- the SDK's own report
    # names the server directly; no spec_by_server match just falls back
    # to the raw server name so nothing is silently dropped.
    assert turn.failed_connector_ids == ["swiggy-instamart"]


def test_subscription_runtime_reports_no_failure_when_the_sdk_reports_none():
    async def fake_query(*, prompt, options):
        yield SystemMessage(subtype="init", data={
            "session_id": "sdk-session-1",
            "mcp_servers": [{"name": "github", "status": "connected"}],
        })
        return
        yield  # pragma: no cover

    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    import asyncio
    turn = asyncio.run(runtime.run_turn(
        "sys", "user", [_spec(ToolPermission("list_issues", "R0"))],
    ))
    assert turn.failed_connector_ids == []


def test_subscription_tool_use_without_result_is_never_successful():
    async def fake_query(*, prompt, options):
        yield AssistantMessage(
            content=[ToolUseBlock(
                id="toolu_missing", name="mcp__github__list_issues", input={},
            )],
            model="claude-test",
        )

    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    import asyncio
    turn = asyncio.run(runtime.run_turn(
        "sys", "user", [_spec(ToolPermission("list_issues", "R0"))]
    ))
    assert turn.tool_calls[0].result is None
    assert turn.tool_calls[0].succeeded is False


def test_subscription_runtime_refuses_cli_managed_connector_credentials():
    called = {"yes": False}

    async def fake_query(*, prompt, options):
        called["yes"] = True
        return
        yield  # pragma: no cover

    spec = ConnectorInvocationSpec(
        connector_id="swiggy-instamart", url="https://mcp.swiggy.com/im",
        tools=(ToolPermission("search_products", "R0"),), cli_managed=True,
    )
    runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=fake_query)
    from orderguard.agent.tools import CliManagedConnectorUnsupported
    import asyncio
    with pytest.raises(CliManagedConnectorUnsupported):
        asyncio.run(runtime.run_turn("sys", "user", [spec]))
    assert called["yes"] is False


def test_api_runtime_refuses_a_cli_managed_connector_outright():
    from orderguard.agent.runtime.api_runtime import CliManagedConnectorUnsupported

    spec = ConnectorInvocationSpec(
        connector_id="swiggy-instamart", url="https://mcp.swiggy.com/im",
        tools=(ToolPermission("search_products", "R0"),), cli_managed=True,
    )
    runtime = AnthropicApiRuntime(api_key="sk-ant-test", client=MagicMock())
    import asyncio
    with pytest.raises(CliManagedConnectorUnsupported):
        asyncio.run(runtime.run_turn("sys", "user", [spec]))


def test_both_runtimes_reject_r3_identically_given_the_same_spec():
    """The parity that actually matters: not identical wire config (proven
    false above), but identical refusal at the one shared boundary."""
    api_runtime = AnthropicApiRuntime(api_key="sk-ant-test", client=MagicMock())
    sub_runtime = SubscriptionAgentRuntime(oauth_token="fake-token", query_fn=MagicMock())

    import asyncio
    spec = [_spec(ToolPermission("pay", "R3"))]
    with pytest.raises(FinancialToolExposureError):
        asyncio.run(api_runtime.run_turn("sys", "user", spec))
    with pytest.raises(FinancialToolExposureError):
        asyncio.run(sub_runtime.run_turn("sys", "user", spec))


def test_api_and_subscription_results_have_semantic_parity():
    raw_result = [{
        "type": "text",
        "text": '{"issues":[{"number":5,"title":"Parity","state":"open","html_url":"https://github.com/x/y/issues/5","user":{"login":"octo"}}]}',
    }]

    fake_client = MagicMock()
    fake_client.beta.messages.create.return_value = SimpleNamespace(
        id="msg_api",
        content=[
            SimpleNamespace(
                type="mcp_tool_use", id="tool_api", server_name="github",
                name="list_issues", input={"owner": "x", "repo": "y"},
            ),
            SimpleNamespace(
                type="mcp_tool_result", tool_use_id="tool_api",
                content=raw_result, is_error=False,
            ),
        ],
        stop_reason="end_turn", usage={"input_tokens": 10},
    )

    async def fake_query(*, prompt, options):
        yield AssistantMessage(
            content=[ToolUseBlock(
                id="tool_sub", name="mcp__github__list_issues",
                input={"owner": "x", "repo": "y"},
            )],
            model="claude-test", stop_reason="tool_use",
        )
        yield UserMessage(content=[ToolResultBlock(
            tool_use_id="tool_sub", content=raw_result, is_error=False,
        )])

    import asyncio
    spec = [_spec(ToolPermission("list_issues", "R0"))]
    api_turn = asyncio.run(AnthropicApiRuntime(
        api_key="sk-ant-test", client=fake_client,
    ).run_turn("sys", "user", spec))
    sub_turn = asyncio.run(SubscriptionAgentRuntime(
        oauth_token="fake", query_fn=fake_query,
    ).run_turn("sys", "user", spec))

    api_call, sub_call = api_turn.tool_calls[0], sub_turn.tool_calls[0]
    assert (
        api_call.connector_id, api_call.tool_name, api_call.arguments,
        api_call.result, api_call.succeeded,
    ) == (
        sub_call.connector_id, sub_call.tool_name, sub_call.arguments,
        sub_call.result, sub_call.succeeded,
    )

    from orderguard.agent.normalizer import normalize
    assert normalize(
        api_call, capability="DEV_TASK", risk_tier="R0", provenance="api:github",
    ).payload == normalize(
        sub_call, capability="DEV_TASK", risk_tier="R0", provenance="subscription:github",
    ).payload
