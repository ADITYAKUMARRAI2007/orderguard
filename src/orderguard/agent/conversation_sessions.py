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
    "was_image_ever_attached", "ever_attempted_connector_ids",
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
    # Sticky, cumulative set of connector ids REALLY attempted (a real tool
    # call, evidence from orchestrator.py's own attempted_connector_ids --
    # never the model's text) anywhere in this thread so far. Real,
    # live-found gap (2026-09-03, see FAILURE_LOG.md F-044): a model that
    # falsely claimed a connector had failed on turn 1 went on to simply
    # trust its OWN earlier claim on every later turn, never attempting
    # that connector again even though it stayed genuinely eligible and
    # connected the whole time -- a prompt telling it not to trust past
    # claims did not reliably stop it from doing exactly that. This set is
    # what lets a later turn state, as a fact next to the user's own
    # message rather than as persuasion, "X has not actually been tried
    # yet in this conversation" -- a claim the model cannot out-reason
    # because it is freshly computed and re-stated every turn.
    attempted_connector_ids_json: str = Field(default="[]")
    updated_at: datetime = Field(default_factory=_now)


def conversation_sessions_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set -- see db.py."""
    engine = make_engine(path)
    _migrate_columns(engine)
    return engine


def _migrate_columns(engine: Engine) -> None:
    """Patches a table created by an EARLIER version of this module (this
    one shipped, live, in production, before these columns existed --
    unlike ``connector_accounts.py``'s equivalent migration, this cannot
    assume "a fresh Postgres database already has every column" holds;
    the real deployed Postgres table here does not. ``create_all`` only
    ever adds whole new tables, never new columns on an existing one, on
    either dialect.
    """
    additions = {
        "image_ever_attached": ("BOOLEAN NOT NULL DEFAULT 0", "BOOLEAN NOT NULL DEFAULT FALSE"),
        "attempted_connector_ids_json": ("VARCHAR NOT NULL DEFAULT '[]'", "VARCHAR NOT NULL DEFAULT '[]'"),
    }
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            rows = connection.exec_driver_sql("PRAGMA table_info(conversationsessionrecord)").fetchall()
            existing = {row[1] for row in rows}
            for name, (sqlite_ddl, _) in additions.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE conversationsessionrecord ADD COLUMN {name} {sqlite_ddl}"
                    )
    else:
        with engine.begin() as connection:
            for name, (_, pg_ddl) in additions.items():
                connection.exec_driver_sql(
                    f"ALTER TABLE conversationsessionrecord ADD COLUMN IF NOT EXISTS {name} {pg_ddl}"
                )


def save_conversation_session(
    engine: Engine, session_id: str, category: str, session_context: dict, *,
    image_attached: bool = False,
    attempted_connector_ids: list[str] | None = None,
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
        if attempted_connector_ids:
            existing_ids = set(json.loads(row.attempted_connector_ids_json or "[]"))
            row.attempted_connector_ids_json = json.dumps(sorted(existing_ids | set(attempted_connector_ids)))
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


def ever_attempted_connector_ids(engine: Engine, session_id: str, category: str) -> frozenset[str]:
    with Session(engine) as db:
        row = db.exec(
            select(ConversationSessionRecord).where(
                ConversationSessionRecord.session_id == session_id,
                ConversationSessionRecord.category == category,
            )
        ).first()
        if row is None:
            return frozenset()
        return frozenset(json.loads(row.attempted_connector_ids_json or "[]"))
