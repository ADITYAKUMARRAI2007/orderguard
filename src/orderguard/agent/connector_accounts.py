"""Encrypted, runtime-independent connector credential storage.

Both ``AnthropicApiRuntime`` and ``SubscriptionAgentRuntime`` need the same
thing here: a bearer token for an external connector (Swiggy, GitHub),
obtained through our own OAuth flow — never through a runtime's own
inference auth, and never extracted from Claude Code's local credential
store (see DECISIONS.md's standing refusal on that point). Verifying the
Agent SDK's actual OAuth behavior directly (code.claude.com/docs/en/agent-sdk/mcp,
2026-08-31) is what confirmed this: the SDK "doesn't open a browser or run an
interactive OAuth flow" and expects the caller's own application to supply an
access token via the server's ``headers`` — so there is no shortcut here for
either runtime.

``owner_ref`` exists so this model is not inherently process-global, per an
explicit build correction. This is a **LOCAL_SINGLE_USER** build: every row
uses the fixed profile ``LOCAL_PROFILE``. A **MULTI_USER_HOSTED** deployment
would need real authenticated-user ownership and isolation before this table
(or the BYOK key store in ``runtime_settings.py``) could safely serve more
than one person — that is explicitly not built here, and nothing here should
be read as claiming otherwise.

Same shared-SQLite-connection gap as ledger.py/capability.py (see
their docstrings): db.py::make_engine's one StaticPool connection for
":memory:"/file SQLite is unsafe for two real OS threads to call
commit() on at the same instant. Guarded the same way, for the same
reason.
"""

from __future__ import annotations

import threading
import base64
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, select

from ..db import make_engine


_CONNECTOR_ACCOUNTS_LOCK = threading.Lock()

__all__ = [
    "LOCAL_PROFILE", "ConnectorAccount", "accounts_engine",
    "ConnectorAccountStore", "MissingConnectorTokenKey", "generate_pkce_pair",
]

LOCAL_PROFILE = "local"
_DEFAULT_PATH = Path("data/connector_accounts.db")

Status = Literal["CONNECTED", "AUTH_REQUIRED", "EXPIRED", "ERROR", "DISCONNECTED"]
AuthStrategy = Literal["NONE", "OAUTH_BEARER", "API_KEY", "STATIC_HEADER", "CUSTOM"]


def _now() -> datetime:
    # Naive UTC, deliberately: SQLite round-trips datetimes without tzinfo,
    # so an aware ``_now()`` compared against a value just read back from the
    # database raises ``TypeError: can't compare offset-naive and
    # offset-aware datetimes`` the moment a token actually has an expiry —
    # exactly the case ``_is_expired`` exists for. Naive-but-always-UTC
    # avoids the mismatch outright.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ConnectorAccount(SQLModel, table=True):
    """Same shape/engine pattern as ``audit.AuditEvent`` / ``ledger`` tables."""

    id: int | None = Field(default=None, primary_key=True)
    account_id: str = Field(default_factory=lambda: uuid.uuid4().hex, index=True)
    owner_ref: str = Field(default=LOCAL_PROFILE, index=True)
    connector_id: str = Field(index=True)
    auth_strategy: str = "CUSTOM"
    status: str = "DISCONNECTED"
    access_token_encrypted: bytes | None = None
    refresh_token_encrypted: bytes | None = None
    expires_at: datetime | None = None
    scopes: str = ""
    external_account_ref: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    # Real, verified connection health from the LAST actual mission turn
    # that touched this connector -- the Agent SDK's own MCP handshake
    # report, never a model claim and never inferred from `expires_at`
    # alone. Real, live-found gap (2026-09-04, see FAILURE_LOG.md F-044's
    # fourth addendum): `status`/`expires_at` above only ever reflect
    # whether a token row exists and whether WE locally think it should
    # still be valid -- neither one can catch a token that was silently
    # revoked or expired server-side without also updating our own record.
    # A user reported the Connectors page showing "CONNECTED" the entire
    # time a real connector was verifiably failing every turn; this is
    # what lets that page show truth instead of a stale local guess.
    last_mcp_status: str | None = None
    last_mcp_checked_at: datetime | None = None
    # A real saved address id/label from THIS connector's own get_addresses,
    # set explicitly by the user (never guessed) -- see FAILURE_LOG.md F-048.
    # When set, orchestrator.py states it to the model as a deterministic
    # fact so a fresh search goes straight to it instead of asking "which
    # address?" every new conversation. Independent of F-048's own fix
    # (carrying the SEARCH's real address through to approval); this is
    # what lets the search itself stop asking in the first place.
    default_address_id: str | None = None
    default_address_label: str | None = None


def accounts_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    engine = make_engine(path)
    _migrate_account_columns(engine)
    return engine


def _migrate_account_columns(engine: Engine) -> None:
    """Add fields introduced after the first local build without dropping
    existing encrypted rows. SQLite cannot add all constraints in place, so
    account_id uniqueness remains enforced by owner/connector access patterns
    for migrated local databases; fresh databases receive the full model.

    Handles both dialects — real, live-found gap (2026-09-04, see
    FAILURE_LOG.md F-042/F-044): this table shipped to the live Postgres
    database long before ``last_mcp_status``/``last_mcp_checked_at``
    existed, so "a fresh Postgres database already has every column"
    (true when this function was first written, for the OLDER columns
    below) does not hold for these new ones. ``create_all`` only ever adds
    whole new tables, never new columns on an existing one, on either
    dialect.
    """
    sqlite_additions = {
        "account_id": "VARCHAR NOT NULL DEFAULT ''",
        "auth_strategy": "VARCHAR NOT NULL DEFAULT 'CUSTOM'",
        "refresh_token_encrypted": "BLOB",
        "external_account_ref": "VARCHAR NOT NULL DEFAULT ''",
        "last_mcp_status": "VARCHAR",
        "last_mcp_checked_at": "TIMESTAMP",
        "default_address_id": "VARCHAR",
        "default_address_label": "VARCHAR",
    }
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            rows = connection.exec_driver_sql("PRAGMA table_info(connectoraccount)").fetchall()
            existing = {row[1] for row in rows}
            for name, ddl in sqlite_additions.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE connectoraccount ADD COLUMN {name} {ddl}"
                    )
    else:
        pg_additions = {
            "last_mcp_status": "VARCHAR",
            "last_mcp_checked_at": "TIMESTAMP",
            "default_address_id": "VARCHAR",
            "default_address_label": "VARCHAR",
        }
        with engine.begin() as connection:
            # Real, live-found gap (2026-09-04): Postgres DDL takes an
            # ACCESS EXCLUSIVE lock with NO default timeout -- an orphaned
            # connection from any earlier crashed/redeployed instance
            # (this project has had many, across a long live session)
            # holding even a weak lock on this table blocks ALTER TABLE
            # forever, silently hanging the entire deploy before uvicorn
            # ever starts (observed directly: two consecutive deploys hung
            # at exactly this point with zero error output). A short
            # lock_timeout makes that fail fast and loud instead.
            connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
            for name, ddl in pg_additions.items():
                connection.exec_driver_sql(
                    f"ALTER TABLE connectoraccount ADD COLUMN IF NOT EXISTS {name} {ddl}"
                )


class MissingConnectorTokenKey(RuntimeError):
    """CONNECTOR_TOKEN_KEY isn't set — refuses to store or read a token
    rather than fall back to storing it in the clear."""


def _fernet_from_env() -> Fernet:
    key = os.getenv("CONNECTOR_TOKEN_KEY", "")
    if not key:
        raise MissingConnectorTokenKey(
            "CONNECTOR_TOKEN_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" '
            "and add it to .env (name only, never commit the value)."
        )
    return Fernet(key.encode())


class ConnectorAccountStore:
    def __init__(
        self,
        engine: Engine,
        owner_ref: str = LOCAL_PROFILE,
        fernet: Fernet | None = None,
    ) -> None:
        self._engine = engine
        self._owner_ref = owner_ref
        self._fernet = fernet

    @property
    def owner_ref(self) -> str:
        return self._owner_ref

    def _cipher(self) -> Fernet:
        return self._fernet or _fernet_from_env()

    def check_encryption_ready(self) -> None:
        """Raises ``MissingConnectorTokenKey`` immediately if this store
        cannot encrypt a token right now — the same check ``store_token``
        would hit lazily, exposed so a caller (app.py's startup hook) can
        surface it before, not after, a real external OAuth round-trip."""
        self._cipher()

    def is_connected(self, connector_id: str) -> bool:
        row = self._get(connector_id)
        return bool(row and row.status == "CONNECTED" and not self._is_expired(row))

    def bearer_token(self, connector_id: str) -> str | None:
        row = self._get(connector_id)
        if not row or not row.access_token_encrypted or self._is_expired(row):
            return None
        try:
            return self._cipher().decrypt(row.access_token_encrypted).decode()
        except InvalidToken:
            return None

    def store_token(
        self,
        connector_id: str,
        access_token: str,
        expires_in_seconds: int | None,
        scopes: str = "",
        *,
        auth_strategy: AuthStrategy = "OAUTH_BEARER",
        refresh_token: str | None = None,
        external_account_ref: str = "",
    ) -> None:
        encrypted = self._cipher().encrypt(access_token.encode())
        refresh_encrypted = (
            self._cipher().encrypt(refresh_token.encode()) if refresh_token else None
        )
        expires_at = (
            _now() + timedelta(seconds=expires_in_seconds)
            if expires_in_seconds is not None else None
        )
        with _CONNECTOR_ACCOUNTS_LOCK, Session(self._engine) as db:
            row = self._select(db, connector_id)
            if row is None:
                row = ConnectorAccount(owner_ref=self._owner_ref, connector_id=connector_id)
            row.status = "CONNECTED"
            if not row.account_id:
                row.account_id = uuid.uuid4().hex
            row.auth_strategy = auth_strategy
            row.access_token_encrypted = encrypted
            row.refresh_token_encrypted = refresh_encrypted
            row.expires_at = expires_at
            row.scopes = scopes
            row.external_account_ref = external_account_ref
            row.updated_at = _now()
            db.add(row)
            db.commit()

    def disconnect(self, connector_id: str) -> None:
        with _CONNECTOR_ACCOUNTS_LOCK, Session(self._engine) as db:
            row = self._select(db, connector_id)
            if row is not None:
                row.status = "DISCONNECTED"
                row.access_token_encrypted = None
                row.refresh_token_encrypted = None
                row.updated_at = _now()
                db.add(row)
                db.commit()

    def status(self, connector_id: str) -> Status:
        row = self._get(connector_id)
        if row is None:
            return "AUTH_REQUIRED"
        if row.status == "CONNECTED" and self._is_expired(row):
            return "EXPIRED"
        return row.status  # type: ignore[return-value]

    def record_mcp_status(self, connector_id: str, status: str) -> None:
        """Real, verified evidence from an actual mission turn's MCP
        handshake — see FAILURE_LOG.md F-044's fourth addendum. Only
        updates an EXISTING account row; a connector with no stored token
        has nothing to correct here, and this method never creates a row
        or grants a connection on its own."""
        with _CONNECTOR_ACCOUNTS_LOCK, Session(self._engine) as db:
            row = self._select(db, connector_id)
            if row is None:
                return
            row.last_mcp_status = status
            row.last_mcp_checked_at = _now()
            db.add(row)
            db.commit()

    def set_default_address(self, connector_id: str, address_id: str, label: str) -> None:
        """Persist the user's own chosen default delivery address for this
        connector, from a real saved address id (never guessed or invented)
        -- see FAILURE_LOG.md F-048. Only updates an existing account row,
        same as ``record_mcp_status``: a connector with no stored token has
        no delivery context to set a default for."""
        with _CONNECTOR_ACCOUNTS_LOCK, Session(self._engine) as db:
            row = self._select(db, connector_id)
            if row is None:
                return
            row.default_address_id = address_id
            row.default_address_label = label
            db.add(row)
            db.commit()

    def default_address(self, connector_id: str) -> tuple[str | None, str | None]:
        """``(address_id, label)`` the user set as this connector's default,
        or ``(None, None)`` if none has been set."""
        row = self._get(connector_id)
        if row is None:
            return None, None
        return row.default_address_id, row.default_address_label

    def mcp_health(self, connector_id: str) -> tuple[str | None, datetime | None]:
        """The last REAL, verified MCP status for this connector and when
        it was checked — ``(None, None)`` if a mission has never actually
        attempted it yet this deployment, or no account row exists."""
        row = self._get(connector_id)
        if row is None:
            return None, None
        return row.last_mcp_status, row.last_mcp_checked_at

    def _get(self, connector_id: str) -> ConnectorAccount | None:
        with _CONNECTOR_ACCOUNTS_LOCK, Session(self._engine) as db:
            return self._select(db, connector_id)

    def _select(self, db: Session, connector_id: str) -> ConnectorAccount | None:
        return db.exec(
            select(ConnectorAccount).where(
                ConnectorAccount.owner_ref == self._owner_ref,
                ConnectorAccount.connector_id == connector_id,
            )
        ).first()

    def _is_expired(self, row: ConnectorAccount) -> bool:
        return row.expires_at is not None and row.expires_at <= _now()


def generate_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge), S256 per RFC 7636 — used by the
    Swiggy OAuth 2.1 connect flow (see ``swiggy_oauth.py``)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge
