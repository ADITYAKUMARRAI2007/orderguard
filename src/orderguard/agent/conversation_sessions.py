"""Durable storage for per-(session_id, category) conversation continuation
state -- the runtime's own opaque resume token (the Agent SDK's `resume`
session id, or the Messages API's replayed history; see
``runtime/base.py::AgentTurnResult``).

Was an in-memory ``dict[tuple[str, str], dict]`` in app.py, explicitly
documented there as an accepted LOCAL_SINGLE_USER tradeoff ("a restart just
means starting a fresh conversation"). Real, live-reproduced cost of that
tradeoff: a backend redeployed several times in one active testing session
silently wiped a user's in-progress conversation each time -- the agent
"forgot" a budget or address it had just asked for and gotten an answer to,
with no visible cause. Same class of gap as every other table this project
moved off process-local state onto ``db.py`` (FAILURE_LOG.md F-035); a
redeploy is not rare enough here to keep treating it as an edge case.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, select

from ..db import make_engine

__all__ = [
    "conversation_sessions_engine", "save_conversation_session", "load_conversation_session",
    "was_image_ever_attached",
]

_DEFAULT_PATH = Path("data/conversation_sessions.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationSessionRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    category: str = Field(index=True)
    session_context_json: str
    # Sticky for the life of this (session_id, category) thread -- real,
    # live-found gap (2026-09-03, see FAILURE_LOG.md F-042): eligibility
    # widening for an image-attached turn (orchestrator.py's
    # _IMAGE_FALLBACK_EXTRA_CATEGORIES) only checked THIS turn's own
    # ``image`` argument. A continuation reply carries no image of its own
    # even when it's replying within a conversation that started with one,
    # so a connector genuinely offered in turn 1 (visible in the resumed
    # SDK session's own history) silently vanished from the tool list on
    # turn 2 -- and the model, reacting to that real discontinuity,
    # narrated it as the connector having "disconnected" rather than what
    # actually happened. Once True for a thread, stays True -- an image
    # earlier in the SAME conversation is what matters, not only the most
    # recent turn.
    image_ever_attached: bool = Field(default=False)
    updated_at: datetime = Field(default_factory=_now)


def conversation_sessions_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set -- see db.py."""
    engine = make_engine(path)
    _migrate_image_ever_attached_column(engine)
    return engine


def _migrate_image_ever_attached_column(engine: Engine) -> None:
    """Patches a table created by an EARLIER version of this module (this
    one shipped, live, in production, before ``image_ever_attached``
    existed -- unlike ``connector_accounts.py``'s equivalent migration,
    this cannot assume "a fresh Postgres database already has every
    column" holds; the real deployed Postgres table here does not.
    ``create_all`` only ever adds whole new tables, never new columns on
    an existing one, on either dialect.
    """
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            rows = connection.exec_driver_sql("PRAGMA table_info(conversationsessionrecord)").fetchall()
            existing = {row[1] for row in rows}
            if "image_ever_attached" not in existing:
                connection.exec_driver_sql(
                    "ALTER TABLE conversationsessionrecord "
                    "ADD COLUMN image_ever_attached BOOLEAN NOT NULL DEFAULT 0"
                )
    else:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE conversationsessionrecord "
                "ADD COLUMN IF NOT EXISTS image_ever_attached BOOLEAN NOT NULL DEFAULT FALSE"
            )


def save_conversation_session(
    engine: Engine, session_id: str, category: str, session_context: dict, *,
    image_attached: bool = False,
) -> None:
    with Session(engine) as db:
        row = db.exec(
            select(ConversationSessionRecord).where(
                ConversationSessionRecord.session_id == session_id,
                ConversationSessionRecord.category == category,
            )
        ).first()
        if row is None:
            row = ConversationSessionRecord(session_id=session_id, category=category, session_context_json="{}")
        row.session_context_json = json.dumps(session_context)
        row.image_ever_attached = row.image_ever_attached or image_attached
        row.updated_at = _now()
        db.add(row)
        db.commit()


def load_conversation_session(engine: Engine, session_id: str, category: str) -> dict | None:
    with Session(engine) as db:
        row = db.exec(
            select(ConversationSessionRecord).where(
                ConversationSessionRecord.session_id == session_id,
                ConversationSessionRecord.category == category,
            )
        ).first()
        if row is None:
            return None
        return json.loads(row.session_context_json)


def was_image_ever_attached(engine: Engine, session_id: str, category: str) -> bool:
    with Session(engine) as db:
        row = db.exec(
            select(ConversationSessionRecord).where(
                ConversationSessionRecord.session_id == session_id,
                ConversationSessionRecord.category == category,
            )
        ).first()
        return bool(row and row.image_ever_attached)
