"""Encrypted, runtime-independent connector credential storage. Round-trips
through a real Fernet key (generated per test, never a fixed one committed
to the repo), never a mock cipher — the encryption itself is the thing being
tested, not just the plumbing around it.
"""

import time

import pytest
from cryptography.fernet import Fernet

from orderguard.agent.connector_accounts import (
    ConnectorAccountStore, MissingConnectorTokenKey, accounts_engine, generate_pkce_pair,
)


@pytest.fixture
def store():
    engine = accounts_engine(":memory:")
    return ConnectorAccountStore(engine, owner_ref="local", fernet=Fernet(Fernet.generate_key()))


def test_a_stored_token_round_trips_through_encryption(store):
    store.store_token("swiggy-instamart", "real-bearer-token", expires_in_seconds=3600)
    assert store.is_connected("swiggy-instamart")
    assert store.bearer_token("swiggy-instamart") == "real-bearer-token"


def test_an_unconnected_connector_has_no_token(store):
    assert not store.is_connected("swiggy-instamart")
    assert store.bearer_token("swiggy-instamart") is None
    assert store.status("swiggy-instamart") == "AUTH_REQUIRED"


def test_disconnect_clears_the_token(store):
    store.store_token("github", "ghp_xxx", expires_in_seconds=None)
    store.disconnect("github")
    assert not store.is_connected("github")
    assert store.bearer_token("github") is None
    assert store.status("github") == "DISCONNECTED"


def test_an_expired_token_is_reported_expired_and_unusable(store):
    store.store_token("swiggy-food", "token", expires_in_seconds=0)
    time.sleep(0.01)
    assert store.status("swiggy-food") == "EXPIRED"
    assert not store.is_connected("swiggy-food")
    assert store.bearer_token("swiggy-food") is None


def test_a_token_with_no_expiry_never_expires(store):
    store.store_token("github", "ghp_xxx", expires_in_seconds=None)
    assert store.is_connected("github")


def test_different_owner_refs_are_isolated():
    engine = accounts_engine(":memory:")
    fernet = Fernet(Fernet.generate_key())
    alice = ConnectorAccountStore(engine, owner_ref="alice", fernet=fernet)
    bob = ConnectorAccountStore(engine, owner_ref="bob", fernet=fernet)

    alice.store_token("github", "alice-token", expires_in_seconds=None)
    assert alice.is_connected("github")
    assert not bob.is_connected("github")


def test_stored_tokens_are_never_stored_in_the_clear():
    from sqlmodel import Session, select

    from orderguard.agent.connector_accounts import ConnectorAccount

    engine = accounts_engine(":memory:")
    store = ConnectorAccountStore(engine, fernet=Fernet(Fernet.generate_key()))
    store.store_token("github", "super-secret-pat", expires_in_seconds=None)

    with Session(engine) as db:
        row = db.exec(select(ConnectorAccount)).one()
    assert b"super-secret-pat" not in row.access_token_encrypted


def test_generic_account_metadata_and_refresh_token_are_encrypted():
    from sqlmodel import Session, select

    from orderguard.agent.connector_accounts import ConnectorAccount

    engine = accounts_engine(":memory:")
    store = ConnectorAccountStore(engine, owner_ref="alice", fernet=Fernet(Fernet.generate_key()))
    store.store_token(
        "github", "access-secret", expires_in_seconds=3600,
        auth_strategy="API_KEY", refresh_token="refresh-secret",
        scopes="repo:read", external_account_ref="octocat",
    )
    with Session(engine) as db:
        row = db.exec(select(ConnectorAccount)).one()
    assert row.account_id
    assert row.owner_ref == "alice"
    assert row.auth_strategy == "API_KEY"
    assert row.external_account_ref == "octocat"
    assert b"access-secret" not in row.access_token_encrypted
    assert b"refresh-secret" not in row.refresh_token_encrypted


def test_missing_token_key_refuses_rather_than_falls_back_to_plaintext():
    import os

    engine = accounts_engine(":memory:")
    store = ConnectorAccountStore(engine)  # no fernet injected, reads env
    old = os.environ.pop("CONNECTOR_TOKEN_KEY", None)
    try:
        with pytest.raises(MissingConnectorTokenKey):
            store.store_token("github", "x", expires_in_seconds=None)
    finally:
        if old is not None:
            os.environ["CONNECTOR_TOKEN_KEY"] = old


def test_check_encryption_ready_surfaces_the_same_error_before_any_token_is_stored():
    """Regression: CONNECTOR_TOKEN_KEY was missing from a real .env, and the
    first sign of it was a bare 500 deep inside the Swiggy OAuth callback —
    after the user had already completed a real, external consent screen and
    burned a single-use authorization code for nothing. check_encryption_ready
    lets a startup hook catch this before any external round-trip happens."""
    import os

    engine = accounts_engine(":memory:")
    store = ConnectorAccountStore(engine)  # no fernet injected, reads env
    old = os.environ.pop("CONNECTOR_TOKEN_KEY", None)
    try:
        with pytest.raises(MissingConnectorTokenKey):
            store.check_encryption_ready()
    finally:
        if old is not None:
            os.environ["CONNECTOR_TOKEN_KEY"] = old


def test_check_encryption_ready_is_silent_when_a_key_is_configured():
    store = ConnectorAccountStore(accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()))
    store.check_encryption_ready()  # must not raise


def test_pkce_pair_is_a_valid_s256_challenge():
    import base64
    import hashlib

    verifier, challenge = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge
