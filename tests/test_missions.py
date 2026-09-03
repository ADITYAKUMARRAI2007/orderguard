"""Commerce Missions: multi-intent decomposition, each sub-intent
independently risk-governed. The one invariant that matters most: nothing
here ever produces an ``Authorization`` — that only exists downstream of the
commerce ``select_offer -> confirm -> gates`` path this module never touches.
"""

from cryptography.fernet import Fernet

from orderguard.agent.connector_accounts import ConnectorAccountStore, accounts_engine
from orderguard.agent.missions import decompose, decompose_intents, run_mission
from orderguard.agent.runtime.base import StubAgentRuntime


def test_decompose_a_single_clause_grocery_request():
    assert decompose("order milk from instamart") == ["COMMERCE_GROCERY"]


def test_decompose_a_single_clause_dev_request():
    assert decompose("check my github issues") == ["DEV_TASK"]


def test_decompose_routes_individual_grocery_item_names_without_the_word_grocery():
    """Real, live-found gap (2026-09-03, see FAILURE_LOG.md): a real
    grocery shopping-list photo's items -- onion, potato, red chili powder
    -- classified as COMMERCE_GENERAL (Shopify's non-grocery demo stores)
    because none of them were the literal word "grocery"/"milk"/
    "instamart"/"vegetables". Swiggy Instamart is the only COMMERCE_GROCERY
    connector, so this is what actually determines which connector a real
    grocery list reaches -- including one read out of an attached image,
    since that's classified on text alone same as any typed request."""
    assert decompose("buy onions and potatoes") == ["COMMERCE_GROCERY", "COMMERCE_GROCERY"]


def test_decompose_splits_on_and():
    categories = decompose("order dinner and check my github issues")
    assert categories == ["COMMERCE_FOOD", "DEV_TASK"]


def test_decompose_falls_back_to_commerce_general_never_drops_a_clause():
    categories = decompose("buy a birthday gift")
    assert categories == ["COMMERCE_GENERAL"]


def test_decomposition_retains_each_clause_for_independent_routing():
    intents = decompose_intents("find my github issues and buy coffee")
    assert [(i.text, i.capability, i.risk_tier) for i in intents] == [
        ("find my github issues", "DEV_TASK", "R0"),
        ("buy coffee", "COMMERCE_GENERAL", "R0"),
    ]
    assert intents[0].intent_id != intents[1].intent_id


def test_a_trailing_modifier_clause_folds_into_the_request_it_modifies():
    """Regression for a real, reproduced incident: "order milk under 60 and
    for work address and dinner under 500 and for same address" split into
    FOUR clauses — two of which ("for work address", "for same address")
    matched no keyword on their own and became their own bogus
    COMMERCE_GENERAL intents asking "what are you ordering?" about an order
    that was never lost. Both were trailing modifiers of the clause right
    before them, not independent requests, and must fold back into it."""
    intents = decompose_intents(
        "order milk from instamart under 60 and for work address and "
        "dinner also for me for under 500 and for same address"
    )
    assert [(i.capability, i.text) for i in intents] == [
        ("COMMERCE_GROCERY", "order milk from instamart under 60 and for work address"),
        ("COMMERCE_FOOD", "dinner also for me for under 500 and for same address"),
    ]


def test_a_genuinely_new_keyword_less_request_still_stands_alone():
    """The fix above must not eat a real second request just because it
    happens to name no keyword — only a clause that ALSO opens like a
    modifier ("for", "to", "under", ...) is folded."""
    intents = decompose_intents("check my github issues and buy a birthday gift")
    assert [(i.capability, i.text) for i in intents] == [
        ("DEV_TASK", "check my github issues"),
        ("COMMERCE_GENERAL", "buy a birthday gift"),
    ]


async def test_a_mission_runs_one_step_per_decomposed_category():
    store = ConnectorAccountStore(accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()))
    store.store_token("github", "ghp_xxx", expires_in_seconds=None)
    runtime = StubAgentRuntime()

    result = await run_mission(
        message="order dinner and check my github issues", runtime=runtime, accounts=store,
    )

    assert [step.category for step in result.steps] == ["COMMERCE_FOOD", "DEV_TASK"]
    # Nothing in a mission step ever carries an Authorization — that object
    # doesn't even exist in this module's vocabulary.
    for step in result.steps:
        assert not hasattr(step, "authorization")
    assert runtime.calls[0][1] == "check my github issues"


async def test_continue_category_skips_decomposition_and_threads_session_context():
    """Regression for a real, reproduced incident: the model asked "Which
    address should I use for delivery?", the user replied "work address",
    and the keyword classifier — with no memory of the prior turn — routed
    that reply to COMMERCE_GENERAL, which matched no eligible connector.
    continue_category must route the WHOLE reply to the SAME category
    without ever calling the keyword decomposer, and session_context must
    reach the runtime unmodified."""
    store = ConnectorAccountStore(accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()))
    store.store_token("swiggy-instamart", "token", expires_in_seconds=None)
    runtime = StubAgentRuntime()

    result = await run_mission(
        message="work address", runtime=runtime, accounts=store,
        continue_category="COMMERCE_GROCERY",
        session_context={"resume": "sdk-session-abc"},
    )

    # One intent, exactly the category asked for — not re-classified.
    assert [step.category for step in result.steps] == ["COMMERCE_GROCERY"]
    assert len(runtime.calls) == 1
    assert runtime.calls[0][1] == "work address"
    assert runtime.last_session_context == {"resume": "sdk-session-abc"}


async def test_without_continue_category_session_context_is_never_used():
    """A fresh, non-continuation mission must never accidentally inherit
    stale continuation state from a caller that forgot to clear it."""
    store = ConnectorAccountStore(accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()))
    store.store_token("github", "ghp_xxx", expires_in_seconds=None)
    runtime = StubAgentRuntime()

    await run_mission(
        message="check my github issues", runtime=runtime, accounts=store,
        session_context={"resume": "should-be-ignored"},
    )
    assert runtime.last_session_context is None
