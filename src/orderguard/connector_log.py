"""Every check_cart outcome, by merchant — an inspectable, growing record.

Same SQLModel/SQLite pattern as ledger.py and audit.py. This is deliberately
smaller and less strict than either: no idempotency key, no hash chain — just
"which merchant, allowed or blocked, when." The audit chain (audit.py) is the
tamper-evident record of everything; this is a cheap, queryable index over one
slice of it (merchant -> outcome), so a judge or a future session can ask "what
has this actually been checked against" without replaying the whole chain.

Same shared-SQLite-connection gap as ledger.py/capability.py (see
their docstrings): db.py::make_engine's one StaticPool connection for
":memory:"/file SQLite is unsafe for two real OS threads to call
commit() on at the same instant. Guarded the same way, for the same
reason.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, select

from .db import make_engine


_CONNECTOR_LOG_LOCK = threading.Lock()

__all__ = ["ConnectorCheck", "connector_log_engine", "record_check", "checks_for_merchant", "merchants_checked"]

_DEFAULT_PATH = Path("data/connector_log.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConnectorCheck(SQLModel, table=True):
    """One check_cart outcome. No PII, no card data — just the verdict."""

    id: int | None = Field(default=None, primary_key=True)
    merchant: str = Field(index=True)
    allow: bool
    failed_gates_csv: str = ""     # comma-joined GateName strings; empty when allow=True
    checks_passed: int
    checks_total: int
    cart_total_paise: int
    created_at: datetime = Field(default_factory=_now)


def connector_log_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    return make_engine(path)


def record_check(
    engine: Engine, *, merchant: str, allow: bool, failed_gates: list[str],
    checks_passed: int, checks_total: int, cart_total_paise: int,
) -> ConnectorCheck:
    with _CONNECTOR_LOG_LOCK, Session(engine) as db:
        entry = ConnectorCheck(
            merchant=merchant, allow=allow, failed_gates_csv=",".join(failed_gates),
            checks_passed=checks_passed, checks_total=checks_total,
            cart_total_paise=cart_total_paise,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry


def checks_for_merchant(engine: Engine, merchant: str) -> list[ConnectorCheck]:
    with _CONNECTOR_LOG_LOCK, Session(engine) as db:
        return list(
            db.exec(select(ConnectorCheck).where(ConnectorCheck.merchant == merchant))
        )


def merchants_checked(engine: Engine) -> list[str]:
    """Every distinct merchant this server has ever run check_cart against,
    in the order first seen."""
    with _CONNECTOR_LOG_LOCK, Session(engine) as db:
        rows = db.exec(select(ConnectorCheck.merchant).order_by(ConnectorCheck.id))
        seen: list[str] = []
        for merchant in rows:
            if merchant not in seen:
                seen.append(merchant)
        return seen
