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

__all__ = ["conversation_sessions_engine", "save_conversation_session", "load_conversation_session"]

_DEFAULT_PATH = Path("data/conversation_sessions.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationSessionRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    category: str = Field(index=True)
    session_context_json: str
    updated_at: datetime = Field(default_factory=_now)


def conversation_sessions_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set -- see db.py."""
    return make_engine(path)


def save_conversation_session(engine: Engine, session_id: str, category: str, session_context: dict) -> None:
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
