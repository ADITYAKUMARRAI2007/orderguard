"""The orchestrator exposes only eligible, backend-authenticated connectors."""

from cryptography.fernet import Fernet

from orderguard.agent.connector_accounts import ConnectorAccountStore, accounts_engine
import pytest

from orderguard.agent.orchestrator import (
    ConnectorProvenanceError, IneligibleConnectorSelectionError, run_agent_turn,
)
from orderguard.agent.runtime.base import AgentTurnResult, ImageInput, StubAgentRuntime, ToolCallEvent


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


async def test_an_attached_image_reaches_the_runtime_unchanged():
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    image = ImageInput(media_type="image/jpeg", data_base64="ZmFrZS1qcGVn")
    await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime,
        accounts=store, image=image,
    )
    assert runtime.last_image is image


async def test_no_image_attached_means_no_image_reaches_the_runtime():
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=store,
    )
    assert runtime.last_image is None


async def test_an_attached_image_with_a_generic_caption_also_reaches_swiggy_instamart():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md): a shopping-
    list photo's caption ("check this out and add to cart") carries none
    of missions.py's COMMERCE_GROCERY keywords, so the mission classifies
    as COMMERCE_GENERAL before the image is ever read -- previously that
    meant only Shopify's non-grocery demo stores were ever reachable, even
    with a real, connected Swiggy Instamart account. An image-attached
    COMMERCE_GENERAL turn must offer Swiggy Instamart too, so the model
    (which can actually read the photo) has a real grocery connector to
    search, not just Shopify."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    image = ImageInput(media_type="image/jpeg", data_base64="ZmFrZS1qcGVn")
    result = await run_agent_turn(
        message="check this out and add to cart", category="COMMERCE_GENERAL",
        runtime=runtime, accounts=store, image=image,
    )
    assert set(result.eligible_connector_ids) == {"shopify", "swiggy-instamart"}


async def test_without_an_image_commerce_general_does_not_widen_to_swiggy_instamart():
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    result = await run_agent_turn(
        message="check this out and add to cart", category="COMMERCE_GENERAL",
        runtime=runtime, accounts=store,
    )
    assert result.eligible_connector_ids == ["shopify"]


async def test_image_context_established_widens_eligibility_without_a_new_image():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md F-042): a
    continuation reply carries no image of its own even when it's replying
    within a conversation that started with one. A connector genuinely
    offered on an earlier turn (visible in the resumed SDK session's own
    history) must not silently vanish from the tool list on the next turn
    just because that turn's own ``image`` argument is None -- the model,
    correctly observing its own tool list shrink, narrated that as the
    connector having "disconnected" when nothing had failed at all."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    result = await run_agent_turn(
        message="work address, budget 500", category="COMMERCE_GENERAL",
        runtime=runtime, accounts=store, image=None, image_context_established=True,
    )
    assert set(result.eligible_connector_ids) == {"shopify", "swiggy-instamart"}


async def test_a_continuation_turn_notes_an_unverified_connector_deterministically():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md F-044): a model
    that falsely claimed a connector had failed on one turn went on to
    simply trust its OWN earlier claim on every later turn, never
    attempting that connector again even though it stayed genuinely
    eligible and connected -- a prompt telling it not to trust past claims
    did not reliably stop this. A deterministic note, re-computed fresh
    every turn from real evidence and appended to the message itself
    (never `message` verbatim, which for_query/extract_budget_minor must
    still see unmodified), is what this asserts exists."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="under 500", category="COMMERCE_GENERAL", runtime=runtime, accounts=store,
        session_context={"resume": "sdk-1"}, image_context_established=True,
        previously_attempted_connector_ids=frozenset(),
    )
    sent_message = runtime.calls[0][1]
    assert sent_message.startswith("under 500")
    assert "swiggy-instamart" in sent_message
    assert "you have not called" in sent_message
    assert "Before you write anything else, call it" in sent_message


async def test_a_connector_already_attempted_gets_no_note():
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="under 500", category="COMMERCE_GENERAL", runtime=runtime, accounts=store,
        session_context={"resume": "sdk-1"}, image_context_established=True,
        previously_attempted_connector_ids=frozenset({"shopify", "swiggy-instamart"}),
    )
    assert runtime.calls[0][1] == "under 500"


async def test_a_connector_with_a_real_verified_failure_gets_no_retry_nudge():
    """Real, live-found gap (2026-09-04, see FAILURE_LOG.md F-044's
    addendum): a connector whose MCP handshake genuinely failed in an
    earlier turn (real evidence from the SDK's own init message, not a
    model claim) must not keep getting told "you have not called X yet,
    call it" -- that would just be noise pointed at something already
    confirmed broken, not a correction of an unverified claim."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="under 500", category="COMMERCE_GENERAL", runtime=runtime, accounts=store,
        session_context={"resume": "sdk-1"}, image_context_established=True,
        previously_attempted_connector_ids=frozenset(),
        previously_failed_connector_ids=frozenset({"swiggy-instamart"}),
    )
    sent_message = runtime.calls[0][1]
    assert "swiggy-instamart" not in sent_message
    # shopify is still genuinely unverified, so it still gets the note.
    assert "shopify" in sent_message


async def test_a_brand_new_turn_gets_no_note_even_with_unattempted_connectors():
    """No `session_context` means no earlier turn in this thread exists to
    have made a stale claim -- nothing here is yet worth correcting, and
    adding the note anyway would just be noise on every ordinary first
    turn (see this project's existing decomposition tests, which assert
    the runtime receives the message unmodified)."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=store,
    )
    assert runtime.calls[0][1] == "order milk"


async def test_a_default_address_is_stated_to_the_model_even_on_a_brand_new_turn():
    """A user-set default delivery address (FAILURE_LOG.md F-048;
    ConnectorAccountStore.set_default_address) must reach the model on the
    VERY FIRST turn of a conversation, unlike the unverified-connector
    note above -- a fresh mission has no earlier answer to fall back on,
    so this is exactly where Swiggy's own address-required search would
    otherwise make the model ask the user which one to use."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    store.set_default_address("swiggy-instamart", "WORK-ADDR-ID", "Work")
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=store,
    )
    sent_message = runtime.calls[0][1]
    assert sent_message.startswith("order milk")
    assert "swiggy-instamart" in sent_message
    assert "WORK-ADDR-ID" in sent_message
    assert "Work" in sent_message
    assert "without asking" in sent_message


async def test_no_default_address_set_leaves_the_message_unchanged():
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime()
    await run_agent_turn(
        message="order milk", category="COMMERCE_GROCERY", runtime=runtime, accounts=store,
    )
    assert runtime.calls[0][1] == "order milk"


async def test_attempted_connector_ids_reflects_real_tool_calls_not_all_eligible_ones():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md F-041): with
    more than one eligible connector, a turn's own reply text is not
    reliable evidence of what it actually searched -- a live case had the
    model claim a fully-connected, working connector was "disconnected"
    when it had simply never called it. attempted_connector_ids is built
    from the runtime's own real tool_calls, so eligible vs. actually-
    searched can be compared without ever trusting the model's narration."""
    store = _store()
    store.store_token("swiggy-instamart", "our-own-token", expires_in_seconds=None)
    runtime = StubAgentRuntime(AgentTurnResult(
        text="I searched Shopify only.", stop_reason="end_turn", runtime="stub",
        tool_calls=[ToolCallEvent(
            connector_id="shopify", server_name="shopify-0",
            tool_name="search_catalog", arguments={}, execution_id="call-1",
            result={"products": []},
        )],
    ))
    image = ImageInput(media_type="image/jpeg", data_base64="ZmFrZS1qcGVn")
    result = await run_agent_turn(
        message="coffee", category="COMMERCE_GENERAL",
        runtime=runtime, accounts=store, image=image,
    )
    assert set(result.eligible_connector_ids) == {"shopify", "swiggy-instamart"}
    assert result.attempted_connector_ids == ["shopify"]


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
