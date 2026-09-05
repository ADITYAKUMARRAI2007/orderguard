"""The agent's REST surface: fail-closed when unconfigured, never a crash;
BYOK never leaks the key back; a custom connector SSRF attempt is rejected
at the HTTP boundary, not just in the underlying function.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLModelSession, select

from orderguard import app as app_module
from orderguard.agent.conversation_sessions import ConversationSessionRecord

client = TestClient(app_module.app)


def _clear_conversation_sessions() -> None:
    """Conversation continuation state is DB-backed (a real, shared SQLite
    file across test runs, same as every other table here) -- tests that
    reuse a fixed session_id need this instead of the old in-memory dict's
    ``.clear()`` to stay isolated from a previous run's rows."""
    with SQLModelSession(app_module.CONVERSATION_SESSIONS_DB) as db:
        for row in db.exec(select(ConversationSessionRecord)).all():
            db.delete(row)
        db.commit()


@pytest.fixture(autouse=True)
def _clean_runtime_settings(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME", raising=False)
    app_module.RUNTIME_SETTINGS.forget_byok_key()
    yield
    app_module.RUNTIME_SETTINGS.forget_byok_key()


def test_agent_connectors_lists_the_registry():
    resp = client.get("/api/agent/connectors")
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["connectors"]}
    assert {"swiggy-instamart", "swiggy-food", "shopify", "github"} <= ids


def test_no_registered_connector_ever_lists_an_r3_tool_over_the_api():
    resp = client.get("/api/agent/connectors")
    for connector in resp.json()["connectors"]:
        for tool in connector["tools"]:
            assert tool["risk_tier"] != "R3"


def test_runtime_status_reports_nothing_configured_by_default():
    resp = client.get("/api/runtime/status")
    body = resp.json()
    assert body["server_managed_api_key"] is False
    assert body["byok_session_api_key"] is False
    assert body["subscription_runtime"] is False


def test_byok_key_round_trips_and_is_never_returned_in_full():
    resp = client.post("/api/runtime/api-key", json={"api_key": "sk-ant-super-secret-1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["byok_session_api_key"] is True
    assert "sk-ant-super-secret-1234" not in str(body)

    forget = client.post("/api/runtime/api-key/forget")
    assert forget.json()["byok_session_api_key"] is False


def test_agent_run_is_503_when_no_runtime_is_configured():
    resp = client.post("/api/agent/run", json={"message": "order milk", "category": "COMMERCE_GENERAL"})
    assert resp.status_code == 503


def test_agent_run_refuses_and_audits_an_r3_exposure_attempt():
    from orderguard.agent.tools import FinancialToolExposureError

    client.post("/api/runtime/api-key", json={"api_key": "sk-ant-test-key"})
    with patch.object(app_module, "run_agent_turn", new=AsyncMock(
        side_effect=FinancialToolExposureError("swiggy-instamart", "checkout")
    )):
        resp = client.post("/api/agent/run", json={"message": "buy milk", "category": "COMMERCE_GROCERY"})
    assert resp.status_code == 400

    verify = client.get("/api/audit/verify").json()
    assert verify["verified"] is True
    assert any(e["event_type"] == "r3_tool_exposure_blocked" for e in verify["events"])


def test_connecting_github_with_a_manual_token_works():
    resp = client.post("/api/connectors/github/token", json={"token": "ghp_testtoken"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONNECTED"

    status = client.get("/api/connectors/github/status")
    assert status.json()["status"] == "CONNECTED"

    disconnect = client.post("/api/connectors/github/disconnect")
    assert disconnect.json()["status"] == "DISCONNECTED"


def test_generic_credential_entry_masks_and_never_returns_the_secret():
    secret = "github-pat-super-secret-9876"
    resp = client.post("/api/connectors/github/credential", json={
        "credential": secret,
        "auth_strategy": "API_KEY",
        "scopes": "repo:read",
        "external_account_ref": "octocat",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "CONNECTED"
    assert body["credential"] == "••••9876"
    assert secret not in str(body)
    client.post("/api/connectors/github/disconnect")


def test_generic_credential_entry_records_a_real_expiry_when_given_one():
    """Regression for a real incident (2026-09-05): a manually-pushed
    Swiggy token with no expiry recorded looked CONNECTED forever on our
    side while Swiggy's real 5-day token lifetime (swiggy_oauth.py's own
    docstring) quietly ran out underneath it -- reads kept working, writes
    silently stopped persisting, and nothing here ever flagged it."""
    resp = client.post("/api/connectors/github/credential", json={
        "credential": "github-pat-with-a-real-expiry",
        "auth_strategy": "OAUTH_BEARER",
        "expires_in_seconds": 432_000,
    })
    assert resp.status_code == 200
    row = app_module.ACCOUNTS._get("github")
    assert row.expires_at is not None
    client.post("/api/connectors/github/disconnect")


def test_generic_credential_entry_still_defaults_to_no_expiry():
    """A connector with a genuinely non-expiring credential (a static API
    key) must be unaffected -- expiry is opt-in, not assumed."""
    resp = client.post("/api/connectors/github/credential", json={
        "credential": "github-pat-no-expiry",
        "auth_strategy": "API_KEY",
    })
    assert resp.status_code == 200
    row = app_module.ACCOUNTS._get("github")
    assert row.expires_at is None
    client.post("/api/connectors/github/disconnect")


def test_custom_connector_registration_rejects_a_private_ip():
    resp = client.post("/api/connectors/custom", json={"label": "Evil", "url": "https://10.0.0.5/mcp"})
    assert resp.status_code == 400


def test_custom_connector_registration_accepts_a_real_https_url():
    public_dns = [(2, 1, 6, "", ("93.184.216.34", 0))]
    with patch("orderguard.agent.ssrf_guard.socket.getaddrinfo", return_value=public_dns):
        resp = client.post("/api/connectors/custom", json={"label": "Example", "url": "https://example.com/mcp"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Example"


def test_swiggy_connect_returns_a_real_authorize_url_shape_or_a_clean_502():
    """This hits real network (Swiggy's actual /auth/register) unless
    mocked — mocked here so the suite stays offline, matching every other
    test in this project."""
    from orderguard.agent.swiggy_oauth import SwiggyOAuthError

    with patch.object(app_module, "register_client", new=AsyncMock(return_value="client-abc")):
        resp = client.post("/api/connectors/swiggy-instamart/connect")
    assert resp.status_code == 200
    assert resp.json()["authorize_url"].startswith("https://mcp.swiggy.com/auth/authorize?")

    with patch.object(app_module, "register_client", new=AsyncMock(side_effect=SwiggyOAuthError("boom"))):
        failed = client.post("/api/connectors/swiggy-instamart/connect")
    assert failed.status_code == 502


def test_swiggy_connect_rejects_a_connector_that_does_not_use_oauth():
    resp = client.post("/api/connectors/github/connect")
    assert resp.status_code == 400


def test_claude_code_connectors_endpoint_never_crashes_even_if_cli_is_absent(monkeypatch):
    resp = client.get("/api/agent/claude-code-connectors")
    assert resp.status_code == 200
    body = resp.json()
    assert "connectors" in body and "error" in body


def test_claude_code_connectors_endpoint_reports_a_real_detected_connector():
    from unittest.mock import patch

    from orderguard.agent.claude_code_detect import DetectedConnector

    fake = [DetectedConnector(name="swiggy-instamart", url="https://mcp.swiggy.com/im", connected=True, status_text="Connected", cli_managed=True)]
    with patch.object(app_module, "detect_claude_code_connectors", return_value=(fake, "")):
        resp = client.get("/api/agent/claude-code-connectors")
    body = resp.json()
    assert body["connectors"][0]["name"] == "swiggy-instamart"
    assert body["connectors"][0]["usable_by_orderguard"] is False


def test_runtime_mode_can_be_switched_without_restart():
    resp = client.post("/api/runtime/mode", json={"mode": "subscription"})
    assert resp.status_code == 200
    assert resp.json()["active_agent_runtime"] == "subscription"

    back = client.post("/api/runtime/mode", json={"mode": "api"})
    assert back.json()["active_agent_runtime"] == "api"


def test_startup_warns_loudly_when_connector_token_key_is_missing(monkeypatch, capsys):
    """Regression: a real .env never set CONNECTOR_TOKEN_KEY, and the first
    sign of it was a bare 500 deep inside the Swiggy OAuth callback — after
    the user had completed a real external consent screen and burned a
    single-use authorization code. The fix must be visible the moment the
    server starts, not after a real OAuth round-trip."""
    monkeypatch.delenv("CONNECTOR_TOKEN_KEY", raising=False)
    with TestClient(app_module.app):
        pass
    assert "CONNECTOR_TOKEN_KEY is not set" in capsys.readouterr().out


def test_startup_is_silent_when_connector_token_key_is_present(monkeypatch, capsys):
    monkeypatch.setenv("CONNECTOR_TOKEN_KEY", "OHQ3bx-K7JqW9ucN2Mc-abwboaI9SRlQ2JuUVLmsYxc=")
    with TestClient(app_module.app):
        pass
    assert "CONNECTOR_TOKEN_KEY is not set" not in capsys.readouterr().out


def test_agent_run_never_passes_detected_cli_credentials_to_the_orchestrator():
    from unittest.mock import patch

    from orderguard.agent.claude_code_detect import DetectedConnector
    from orderguard.agent.orchestrator import MissionStepResult

    fake = [DetectedConnector(name="swiggy-instamart", url="https://mcp.swiggy.com/im", connected=True, status_text="Connected", cli_managed=True)]
    scripted = MissionStepResult(category="COMMERCE_GROCERY", connector_id="swiggy-instamart", results=[], council=None)

    client.post("/api/runtime/api-key", json={"api_key": "sk-ant-test-key"})
    try:
        with patch.object(app_module, "detect_claude_code_connectors", return_value=(fake, "")), \
             patch.object(app_module, "run_agent_turn", new=AsyncMock(return_value=scripted)) as mocked:
            resp = client.post("/api/agent/run", json={"message": "order milk", "category": "COMMERCE_GROCERY"})
        assert resp.status_code == 200
        _args, kwargs = mocked.call_args
        assert "cli_connected_ids" not in kwargs
    finally:
        app_module.RUNTIME_SETTINGS.forget_byok_key()


def test_a_session_reply_reaches_the_same_conversation_not_a_fresh_one():
    """Regression, end to end at the HTTP boundary: turn 1 returns a runtime
    session_context; turn 2, sent with the same session_id and
    continue_category, must receive that exact context back — proving a
    real follow-up like "work address" would reach the SAME open
    conversation instead of being silently re-decomposed and dropped."""
    from orderguard.agent.orchestrator import MissionStepResult
    from orderguard.agent.missions import MissionResult

    turn1 = MissionResult(
        mission_id="m1", message="order milk from instamart",
        intents=[], steps=[MissionStepResult(
            category="COMMERCE_GROCERY", connector_id="swiggy-instamart",
            results=[], council=None, model_text="Which address?",
            session_context={"resume": "sdk-session-xyz"},
        )],
    )
    turn2 = MissionResult(
        mission_id="m2", message="work address",
        intents=[], steps=[MissionStepResult(
            category="COMMERCE_GROCERY", connector_id="swiggy-instamart",
            results=[], council=None,
        )],
    )

    client.post("/api/runtime/api-key", json={"api_key": "sk-ant-test-key"})
    try:
        with patch.object(app_module, "run_mission", new=AsyncMock(side_effect=[turn1, turn2])) as mocked:
            r1 = client.post("/api/agent/missions/run", json={"message": "order milk from instamart", "session_id": "browser-tab-1"})
            assert r1.status_code == 200

            r2 = client.post("/api/agent/missions/run", json={
                "message": "work address", "session_id": "browser-tab-1",
                "continue_category": "COMMERCE_GROCERY",
            })
            assert r2.status_code == 200

        _args, kwargs = mocked.call_args_list[1]
        assert kwargs["continue_category"] == "COMMERCE_GROCERY"
        assert kwargs["session_context"] == {"resume": "sdk-session-xyz"}
    finally:
        app_module.RUNTIME_SETTINGS.forget_byok_key()
        _clear_conversation_sessions()


def test_an_attached_image_reaches_run_mission_as_a_real_imageinput():
    from orderguard.agent.missions import MissionResult

    result = MissionResult(mission_id="m1", message="milk", intents=[], steps=[])
    client.post("/api/runtime/api-key", json={"api_key": "sk-ant-test-key"})
    try:
        with patch.object(app_module, "run_mission", new=AsyncMock(return_value=result)) as mocked:
            resp = client.post("/api/agent/missions/run", json={
                "message": "order what's in this photo",
                "image_base64": "ZmFrZS1qcGVn",
                "image_media_type": "image/jpeg",
            })
        assert resp.status_code == 200
        _args, kwargs = mocked.call_args
        assert kwargs["image"].media_type == "image/jpeg"
        assert kwargs["image"].data_base64 == "ZmFrZS1qcGVn"
    finally:
        app_module.RUNTIME_SETTINGS.forget_byok_key()


def test_no_image_fields_means_no_image_reaches_run_mission():
    from orderguard.agent.missions import MissionResult

    result = MissionResult(mission_id="m1", message="milk", intents=[], steps=[])
    client.post("/api/runtime/api-key", json={"api_key": "sk-ant-test-key"})
    try:
        with patch.object(app_module, "run_mission", new=AsyncMock(return_value=result)) as mocked:
            resp = client.post("/api/agent/missions/run", json={"message": "order milk"})
        assert resp.status_code == 200
        _args, kwargs = mocked.call_args
        assert kwargs["image"] is None
    finally:
        app_module.RUNTIME_SETTINGS.forget_byok_key()


def test_an_image_field_with_no_matching_partner_is_rejected():
    resp = client.post("/api/agent/missions/run", json={
        "message": "order what's in this photo", "image_base64": "ZmFrZQ==",
        # image_media_type deliberately omitted -- malformed, not "no image"
    })
    assert resp.status_code == 422


def test_an_unsupported_image_media_type_is_rejected():
    resp = client.post("/api/agent/missions/run", json={
        "message": "order what's in this photo",
        "image_base64": "ZmFrZQ==", "image_media_type": "image/svg+xml",
    })
    assert resp.status_code == 422


def test_propose_cart_action_rejects_an_unregistered_cart_write_tool():
    resp = client.post("/api/agent/cart-actions/propose", json={
        "connector_id": "github", "variant_id": "x", "quantity": 1,
        "offer_title": "irrelevant", "offer_price_minor": 100,
    })
    assert resp.status_code == 400


def test_propose_cart_action_stages_a_real_r1_proposal_not_r0_not_auto_executed():
    resp = client.post("/api/agent/cart-actions/propose", json={
        "connector_id": "swiggy-instamart", "variant_id": "SPIN1", "quantity": 2,
        "offer_title": "Heritage Milk", "offer_price_minor": 10800,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_tier"] == "R1"
    assert body["status"] == "PROPOSED"
    assert "Heritage Milk" in body["summary"]
    assert "108.00" in body["summary"]


def test_approve_cart_action_rejects_an_unknown_proposal_id():
    resp = client.post("/api/agent/cart-actions/does-not-exist/approve", json={"address_id": "a1"})
    assert resp.status_code == 404


def test_approve_cart_action_requires_a_connected_account():
    propose = client.post("/api/agent/cart-actions/propose", json={
        "connector_id": "swiggy-instamart", "variant_id": "SPIN1", "quantity": 1,
        "offer_title": "Milk", "offer_price_minor": 2700,
    })
    proposal_id = propose.json()["proposal_id"]
    resp = client.post(f"/api/agent/cart-actions/{proposal_id}/approve", json={"address_id": "a1"})
    # No real ConnectorAccount token is stored for swiggy-instamart in this
    # test client's fresh store, so approval must fail closed, not silently
    # skip the write.
    assert resp.status_code == 409


def test_approve_cart_action_executes_the_exact_stored_arguments_not_a_fresh_decision():
    """The core safety property: what executes is the proposal's own stored
    arguments, verified via the actual add_to_instamart_cart call it makes —
    never re-derived from the approval request."""
    from unittest.mock import AsyncMock, patch

    propose = client.post("/api/agent/cart-actions/propose", json={
        "connector_id": "swiggy-instamart", "variant_id": "SPIN-APPROVED", "quantity": 3,
        "offer_title": "Milk", "offer_price_minor": 2700,
    })
    proposal_id = propose.json()["proposal_id"]

    with patch.object(app_module.ACCOUNTS, "bearer_token", return_value="fake-token"), \
         patch.object(
             app_module, "add_to_instamart_cart",
             new=AsyncMock(return_value={
                 "items_written": [{"spin_id": "SPIN-APPROVED", "quantity": 3}],
                 "preserved_existing_items": 0, "cart_read_skipped_reason": None,
             }),
         ) as mock_write:
        resp = client.post(f"/api/agent/cart-actions/{proposal_id}/approve", json={"address_id": "real-address"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCEEDED"
    assert body["checkout_url"] == "https://www.instamart.in/cart"
    _args, kwargs = mock_write.call_args
    assert kwargs["spin_id"] == "SPIN-APPROVED"
    assert kwargs["quantity"] == 3
    assert kwargs["address_id"] == "real-address"


def test_approve_cart_action_hands_back_the_real_cart_url_even_on_a_failed_write():
    """A write that could not be verified is not proof nothing reached
    Swiggy -- the user's only real recourse is to open the actual cart
    themselves. checkout_url must ride along on the 502, not just the 200."""
    from unittest.mock import AsyncMock, patch
    from orderguard.agent.swiggy_cart import SwiggyCartError

    propose = client.post("/api/agent/cart-actions/propose", json={
        "connector_id": "swiggy-instamart", "variant_id": "SPIN-UNVERIFIED", "quantity": 1,
        "offer_title": "Onion", "offer_price_minor": 5800,
    })
    proposal_id = propose.json()["proposal_id"]

    with patch.object(app_module.ACCOUNTS, "bearer_token", return_value="fake-token"), \
         patch.object(
             app_module, "add_to_instamart_cart",
             new=AsyncMock(side_effect=SwiggyCartError(
                 "update_cart returned no error, but the item is not actually "
                 "in the real cart when read back independently -- nothing was added."
             )),
         ):
        resp = client.post(f"/api/agent/cart-actions/{proposal_id}/approve", json={"address_id": "real-address"})

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "not actually" in detail["message"]
    assert detail["checkout_url"] == "https://www.instamart.in/cart"


def test_approve_cart_action_cannot_be_replayed_twice():
    from unittest.mock import AsyncMock, patch

    propose = client.post("/api/agent/cart-actions/propose", json={
        "connector_id": "swiggy-instamart", "variant_id": "SPIN1", "quantity": 1,
        "offer_title": "Milk", "offer_price_minor": 2700,
    })
    proposal_id = propose.json()["proposal_id"]

    with patch.object(app_module.ACCOUNTS, "bearer_token", return_value="fake-token"), \
         patch.object(app_module, "add_to_instamart_cart", new=AsyncMock(return_value={
             "items_written": [], "preserved_existing_items": 0, "cart_read_skipped_reason": None,
         })):
        first = client.post(f"/api/agent/cart-actions/{proposal_id}/approve", json={"address_id": "a1"})
        second = client.post(f"/api/agent/cart-actions/{proposal_id}/approve", json={"address_id": "a1"})

    assert first.status_code == 200
    assert second.status_code == 409


def test_a_different_session_id_never_sees_another_tabs_conversation():
    from orderguard.agent.orchestrator import MissionStepResult
    from orderguard.agent.missions import MissionResult

    turn1 = MissionResult(
        mission_id="m1", message="order milk", intents=[], steps=[MissionStepResult(
            category="COMMERCE_GROCERY", connector_id="swiggy-instamart",
            results=[], council=None, session_context={"resume": "belongs-to-tab-1"},
        )],
    )
    turn2 = MissionResult(mission_id="m2", message="work address", intents=[], steps=[])

    client.post("/api/runtime/api-key", json={"api_key": "sk-ant-test-key"})
    try:
        with patch.object(app_module, "run_mission", new=AsyncMock(side_effect=[turn1, turn2])) as mocked:
            client.post("/api/agent/missions/run", json={"message": "order milk", "session_id": "tab-1"})
            client.post("/api/agent/missions/run", json={
                "message": "work address", "session_id": "tab-2",
                "continue_category": "COMMERCE_GROCERY",
            })
        _args, kwargs = mocked.call_args_list[1]
        assert kwargs["session_context"] is None
    finally:
        app_module.RUNTIME_SETTINGS.forget_byok_key()
        _clear_conversation_sessions()
