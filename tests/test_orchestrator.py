"""The orchestrator exposes only eligible, backend-authenticated connectors."""

from cryptography.fernet import Fernet

from orderguard.agent.connector_accounts import ConnectorAccountStore, accounts_engine
import pytest

from orderguard.agent.orchestrator import (
    ConnectorProvenanceError, IneligibleConnectorSelectionError, run_agent_turn,
)
from orderguard.agent.runtime.base import AgentTurnResult, StubAgentRuntime, ToolCallEvent


def _store():
    return ConnectorAccountStore(accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()))


async def test_a_cli_only_connected_connector_is_not_exposed():
    runtime = StubAgentRuntime()
    result = await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime,
        accounts=_store(),
    )
    assert result.connector_id is None
    assert runtime.calls == []


async def test_our_own_account_token_is_the_only_connector_credential_source():
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime,
        accounts=store,
    )
    _system, _user, specs = runtime.calls[0]
    assert specs[0].cli_managed is False
    assert specs[0].bearer_token == "our-own-token"


async def test_no_connection_at_all_means_no_eligible_connector():
    runtime = StubAgentRuntime()
    result = await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=_store(),
    )
    assert result.connector_id is None
    assert runtime.calls == []


async def test_shopify_keeps_multiple_stores_eligible_until_after_runtime_search():
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="coffee", category="COMMERCE_GENERAL",
        runtime=runtime, accounts=_store(),
    )
    specs = runtime.calls[0][2]
    assert len(specs) > 1
    assert {spec.connector_id for spec in specs} == {"shopify"}
    assert len({spec.server_name for spec in specs}) == len(specs)
    assert all(spec.resource_ref for spec in specs)


async def test_runtime_selection_outside_eligible_set_is_vetoed():
    runtime = StubAgentRuntime(AgentTurnResult(
        text="", stop_reason="end_turn", runtime="stub",
        tool_calls=[ToolCallEvent(
            connector_id="evil", server_name="evil", tool_name="list_issues",
            arguments={}, execution_id="evil-1", result={"issues": []},
        )],
    ))
    store = _store()
    store.store_token("github", "token", expires_in_seconds=None)
    with pytest.raises(IneligibleConnectorSelectionError):
        await run_agent_turn(
            message="list issues", category="DEV_TASK", runtime=runtime,
            accounts=store,
        )


async def test_the_models_own_text_and_call_duration_are_not_discarded():
    """Regression: a step with zero results (the model made no tool call, or
    asked a clarifying question) looked identical in the UI to "nothing
    happened" — turn.text and how long the call took were computed and then
    thrown away, with no way for a user to see what the model actually said
    or how long it took to say it."""
    runtime = StubAgentRuntime(AgentTurnResult(
        text="I couldn't find a saved delivery address — please add one on Swiggy first.",
        stop_reason="end_turn", runtime="stub", tool_calls=[],
    ))
    store = _store()
    store.store_token("swiggy-instamart", "token", expires_in_seconds=None)
    result = await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=store,
    )
    assert result.model_text == "I couldn't find a saved delivery address — please add one on Swiggy first."
    assert result.results == []
    assert result.duration_ms >= 0


async def test_a_mandatory_prerequisite_call_does_not_break_the_mission():
    """Regression for a real, reproduced incident: Swiggy's own
    search_products tool REQUIRES calling get_addresses first. A real live
    mission correctly called get_addresses then search_products in the same
    turn, and the orchestrator raised on the successful prerequisite call
    before ever reaching the search that actually mattered — the mission
    reported connector_result_unsupported for a call that hadn't failed."""
    store = _store()
    store.store_token("swiggy-instamart", "token", expires_in_seconds=None)
    runtime = StubAgentRuntime(AgentTurnResult(
        text="", stop_reason="end_turn", runtime="stub",
        tool_calls=[
            ToolCallEvent(
                connector_id="swiggy-instamart", server_name="swiggy-instamart",
                tool_name="get_addresses", arguments={}, execution_id="call-1",
                result={"addresses": [{"id": "217934016"}], "total": 1},
            ),
            ToolCallEvent(
                connector_id="swiggy-instamart", server_name="swiggy-instamart",
                tool_name="search_products", arguments={"query": "milk"}, execution_id="call-2",
                result={"products": [{
                    "displayName": "Milk", "inStock": True, "isAvail": True, "productId": "P1",
                    "variations": [{
                        "spinId": "V1", "skuId": "V1SKU", "displayName": "Milk",
                        "price": {"mrp": 30, "offerPrice": 30}, "isInStockAndAvailable": True,
                    }],
                }]},
            ),
        ],
    ))
    result = await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=store,
    )
    # The informational get_addresses call produces no result of its own;
    # only the real search result should surface.
    assert len(result.results) == 1
    assert result.results[0].payload.offers[0].offer.price_minor == 3000


async def test_a_stated_budget_lets_the_council_actually_recommend_something():
    """Without a stated budget, within_budget is None for every offer and
    filter_eligible drops all of them — the Council can never recommend
    anything, no matter how obviously one option is better. A budget stated
    in the user's own message must reach the Council's hard-constraint
    filter for a real recommendation to ever be possible."""
    store = _store()
    store.store_token("swiggy-instamart", "token", expires_in_seconds=None)
    runtime = StubAgentRuntime(AgentTurnResult(
        text="", stop_reason="end_turn", runtime="stub",
        tool_calls=[
            ToolCallEvent(
                connector_id="swiggy-instamart", server_name="swiggy-instamart",
                tool_name="search_products", arguments={"query": "milk"}, execution_id="call-1",
                result={"products": [{
                    "displayName": "Milk", "inStock": True, "isAvail": True, "productId": "P1",
                    "variations": [
                        {
                            "spinId": "CHEAP", "skuId": "CHEAPSKU", "displayName": "Milk 500ml",
                            "price": {"mrp": 30, "offerPrice": 30}, "isInStockAndAvailable": True,
                        },
                        {
                            "spinId": "PRICEY", "skuId": "PRICEYSKU", "displayName": "Milk 1L",
                            "price": {"mrp": 50, "offerPrice": 50}, "isInStockAndAvailable": True,
                        },
                    ],
                }]},
            ),
        ],
    ))
    result = await run_agent_turn(
        message="order milk under 40 rupees", category="COMMERCE_GROCERY",
        runtime=runtime, accounts=store,
    )
    assert result.council is not None
    assert result.council.recommended_id == "swiggy-instamart|CHEAP"
    assert result.council.alternatives_considered == 1
    assert result.council.alternatives_rejected == 1


async def test_connector_resource_provenance_mismatch_is_rejected():
    runtime = StubAgentRuntime(AgentTurnResult(
        text="", stop_reason="end_turn", runtime="stub",
        tool_calls=[ToolCallEvent(
            connector_id="github", server_name="github", resource_ref="evil.example",
            tool_name="list_issues", arguments={}, execution_id="call-1",
            result={"issues": []},
        )],
    ))
    store = _store()
    store.store_token("github", "token", expires_in_seconds=None)
    with pytest.raises(ConnectorProvenanceError):
        await run_agent_turn(
            message="list issues", category="DEV_TASK", runtime=runtime,
            accounts=store,
        )
