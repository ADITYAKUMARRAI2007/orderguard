"""Deterministic connector eligibility: the LLM only ever picks from what
this returns, and it never returns an unauthenticated or unproven connector.
"""

from cryptography.fernet import Fernet

from orderguard.agent.connector_accounts import ConnectorAccountStore, accounts_engine
from orderguard.agent.eligibility import ConnectorEligibilityEngine


def _store():
    return ConnectorAccountStore(accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()))


def test_shopify_is_eligible_without_any_connector_account():
    engine = ConnectorEligibilityEngine(_store())
    eligible = engine.eligible_for("COMMERCE_GENERAL")
    assert any(c.id == "shopify" for c in eligible)


def test_github_is_not_eligible_until_its_account_is_connected():
    engine = ConnectorEligibilityEngine(_store())
    assert not engine.eligible_for("DEV_TASK")


def test_github_becomes_eligible_once_connected():
    store = _store()
    store.store_token("github", "ghp_xxx", expires_in_seconds=None)
    engine = ConnectorEligibilityEngine(store)
    eligible = engine.eligible_for("DEV_TASK")
    assert [c.id for c in eligible] == ["github"]


def test_an_unknown_category_returns_nothing():
    engine = ConnectorEligibilityEngine(_store())
    assert engine.eligible_for("NOT_A_REAL_CATEGORY") == []


def test_swiggy_instamart_needs_its_own_connected_account():
    store = _store()
    engine = ConnectorEligibilityEngine(store)
    assert not engine.eligible_for("COMMERCE_GROCERY")
    store.store_token("swiggy-instamart", "token", expires_in_seconds=None)
    assert [c.id for c in engine.eligible_for("COMMERCE_GROCERY")] == ["swiggy-instamart"]


def test_claude_code_connector_session_is_never_an_orderguard_credential_source():
    engine = ConnectorEligibilityEngine(_store())
    eligible = engine.eligible_for("COMMERCE_GROCERY", cli_connected_ids=frozenset({"swiggy-instamart"}))
    assert eligible == []


def test_cli_connection_for_a_different_connector_id_does_not_leak_eligibility():
    engine = ConnectorEligibilityEngine(_store())
    eligible = engine.eligible_for("COMMERCE_GROCERY", cli_connected_ids=frozenset({"swiggy-food"}))
    assert eligible == []


def test_cli_managed_auth_is_not_eligible_for_either_runtime():
    engine = ConnectorEligibilityEngine(_store())
    eligible = engine.eligible_for(
        "COMMERCE_GROCERY",
        cli_connected_ids=frozenset({"swiggy-instamart"}),
        runtime_name="api",
    )
    assert eligible == []
    assert engine.eligible_for(
        "COMMERCE_GROCERY",
        cli_connected_ids=frozenset({"swiggy-instamart"}),
        runtime_name="subscription",
    ) == []


def test_wrong_owner_profile_is_not_eligible_even_with_a_connected_account():
    store = _store()
    store.store_token("github", "token", expires_in_seconds=None)
    engine = ConnectorEligibilityEngine(store)
    assert engine.eligible_for("DEV_TASK", owner_ref="another-owner") == []


def test_region_and_user_permission_filters_run_before_exposure():
    store = _store()
    store.store_token("swiggy-instamart", "token", expires_in_seconds=None)
    engine = ConnectorEligibilityEngine(store)
    assert engine.eligible_for("COMMERCE_GROCERY", region="US") == []
    assert engine.eligible_for(
        "COMMERCE_GROCERY", region="IN",
        allowed_connector_ids=frozenset({"github"}),
    ) == []
