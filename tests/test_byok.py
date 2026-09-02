"""BYOK Anthropic API key: in-memory only, masked when reported, never
persisted, never returned in full once set.
"""

import os

import pytest

from orderguard.agent.runtime_settings import RuntimeSettings, mask_key


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


def test_mask_key_never_reveals_the_middle():
    assert mask_key("sk-ant-api03-abcdefgh1234") == "sk-ant...1234"


def test_short_keys_are_fully_masked():
    assert mask_key("abc") == "***"


def test_no_key_configured_reports_neither_mode():
    settings = RuntimeSettings()
    key, mode = settings.active_api_key()
    assert key is None and mode is None


def test_server_managed_key_is_read_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key")
    settings = RuntimeSettings()
    key, mode = settings.active_api_key()
    assert key == "sk-ant-server-key"
    assert mode == "server_managed_api_key"


def test_byok_key_takes_precedence_over_server_managed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key")
    settings = RuntimeSettings()
    settings.set_byok_key("sk-ant-user-pasted-key")
    key, mode = settings.active_api_key()
    assert key == "sk-ant-user-pasted-key"
    assert mode == "byok_session_api_key"


def test_forgetting_a_byok_key_falls_back_to_server_managed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-key")
    settings = RuntimeSettings()
    settings.set_byok_key("sk-ant-user-pasted-key")
    settings.forget_byok_key()
    key, mode = settings.active_api_key()
    assert key == "sk-ant-server-key"
    assert mode == "server_managed_api_key"


def test_status_never_includes_the_raw_key():
    settings = RuntimeSettings()
    settings.set_byok_key("sk-ant-super-secret-value-do-not-leak")
    status = settings.status()
    assert "sk-ant-super-secret-value-do-not-leak" not in str(status)
    assert status["byok_masked"] == mask_key("sk-ant-super-secret-value-do-not-leak")


def test_subscription_runtime_status_reflects_env_token(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fake-oauth-token")
    settings = RuntimeSettings()
    assert settings.status()["subscription_runtime"] is True
