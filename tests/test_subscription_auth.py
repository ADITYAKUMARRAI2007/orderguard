"""A rejected subscription token must fail fast and say so.

Regression test for a real incident: a stale CLAUDE_CODE_OAUTH_TOKEN caused
the SDK to retry ten times with exponential backoff, which surfaced in the
UI as a mission that spun forever with no explanation. The token had also
been split across two lines in .env, so it was malformed as well as stale.
"""

from types import SimpleNamespace

import pytest

from orderguard.agent.runtime.subscription_runtime import (
    SubscriptionAgentRuntime,
    SubscriptionAuthFailed,
)
from orderguard.agent.tools import ConnectorInvocationSpec, ToolPermission


def _spec():
    return ConnectorInvocationSpec(
        connector_id="swiggy-instamart",
        url="https://mcp.swiggy.com/im",
        tools=(ToolPermission("search_products", "R0"),),
        bearer_token="connector-account-token",
    )


class _FakeSystemMessage(SimpleNamespace):
    """Mirrors claude_agent_sdk.SystemMessage's shape for the retry event."""


def test_a_401_retry_event_raises_immediately_instead_of_retrying():
    import orderguard.agent.runtime.subscription_runtime as mod

    events = [
        mod.SystemMessage(subtype="init", data={}),
        mod.SystemMessage(
            subtype="api_retry",
            data={"attempt": 1, "max_retries": 10, "error_status": 401, "error": "authentication_failed"},
        ),
    ]

    async def fake_query(*, prompt, options):
        for e in events:
            yield e

    runtime = SubscriptionAgentRuntime(oauth_token="stale-token", query_fn=fake_query)

    import asyncio
    with pytest.raises(SubscriptionAuthFailed) as excinfo:
        asyncio.run(runtime.run_turn("sys", "user", [_spec()]))

    # The message must tell the user exactly how to fix it.
    assert "claude setup-token" in str(excinfo.value)
    assert "single line" in str(excinfo.value)


def test_a_non_auth_retry_does_not_raise():
    """A transient 529/overloaded retry is genuinely worth retrying, so it
    must not be conflated with a dead credential."""
    import orderguard.agent.runtime.subscription_runtime as mod

    async def fake_query(*, prompt, options):
        yield mod.SystemMessage(subtype="api_retry", data={"error_status": 529, "error": "overloaded"})
        yield mod.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0, usage={}, result="done",
        )

    runtime = SubscriptionAgentRuntime(oauth_token="good-token", query_fn=fake_query)

    import asyncio
    result = asyncio.run(runtime.run_turn("sys", "user", [_spec()]))
    assert "done" in result.text


def test_a_stray_anthropic_api_key_is_never_passed_to_the_subscription_subprocess():
    """Regression for a real incident: the host environment carried an
    unrelated `aero_live_…` secret under ANTHROPIC_API_KEY. The CLI prefers
    an API key over a subscription token, so every run 401'd even though the
    token itself was valid (`claude -p` worked with it). Subscription auth is
    subscription auth — the variable must be stripped from the child env."""
    import os
    import orderguard.agent.runtime.subscription_runtime as mod

    captured = {}

    async def fake_query(*, prompt, options):
        captured["env"] = options.env
        yield mod.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0, usage={}, result="ok",
        )

    os.environ["ANTHROPIC_API_KEY"] = "aero_live_some_other_services_secret"
    try:
        runtime = SubscriptionAgentRuntime(oauth_token="valid-token", query_fn=fake_query)
        import asyncio
        asyncio.run(runtime.run_turn("sys", "user", [_spec()]))
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    env = captured["env"]
    # The SDK overlays options.env onto its own inherited os.environ copy
    # ({**inherited_os_environ, **options.env} — verified directly against
    # the installed SDK's transport), so merely omitting a key does nothing:
    # it must be overridden with an explicit falsy value for the merge to
    # suppress whatever the host process inherited.
    assert env["ANTHROPIC_API_KEY"] == ""
    # A host-exported gateway endpoint would send the OAuth token to the
    # wrong service and 401 just as surely as a stray API key.
    assert env["ANTHROPIC_BASE_URL"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "valid-token"


def test_the_subprocess_is_scoped_to_an_isolated_home_not_the_operators():
    """Regression for a real, reproduced incident: on a machine already
    logged into the `claude` CLI interactively (true of every machine this
    demo runs on, since that login is how `claude setup-token` itself gets
    generated), the SDK's subprocess found that interactive session's own
    local state under the inherited HOME and every call 401'd — even though
    the identical CLAUDE_CODE_OAUTH_TOKEN authenticated correctly via a
    plain `claude -p` call. Verified live: pointing the subprocess at an
    empty, isolated HOME made the same token succeed reliably, twice in a
    row, through this exact runtime's own run_turn()."""
    import orderguard.agent.runtime.subscription_runtime as mod

    captured = {}

    async def fake_query(*, prompt, options):
        captured["env"] = options.env
        yield mod.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0, usage={}, result="ok",
        )

    runtime = SubscriptionAgentRuntime(oauth_token="valid-token", query_fn=fake_query)
    import asyncio
    asyncio.run(runtime.run_turn("sys", "user", [_spec()]))

    home = captured["env"]["HOME"]
    assert home == str(mod._SUBPROCESS_HOME.resolve())
    # Scoped to this project's own data/ directory, never the operator's
    # real home — that's the whole point of the isolation.
    assert home.endswith("data/subscription_runtime_home")


def test_the_runtime_does_not_mutate_the_servers_own_environment():
    """The token must ride in the child env, not be written into this
    process's os.environ where concurrent requests could clobber it."""
    import os
    import orderguard.agent.runtime.subscription_runtime as mod

    async def fake_query(*, prompt, options):
        yield mod.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0, usage={}, result="ok",
        )

    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    runtime = SubscriptionAgentRuntime(oauth_token="scoped-token", query_fn=fake_query)
    import asyncio
    asyncio.run(runtime.run_turn("sys", "user", [_spec()]))
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") is None
