"""Durable storage for ``lifecycle.ActionProposal``, cart-write flavor only.

Was an in-memory ``dict[str, ActionProposal]`` in app.py — real, reproduced
gap: a proposal staged by ``propose_cart_action`` and not yet approved does
not survive a backend restart on a host that restarts often (Render's free
tier). A user who staged a real cart write, then took a moment to pick an
address, could come back to "unknown or expired proposal" for no reason
visible to them. Same class of problem as every other table this project
moved off local-process/ephemeral-disk state onto ``db.py`` — see
FAILURE_LOG.md F-035.

``ActionProposal`` itself (agent/lifecycle.py) stays a plain dataclass — it
is also used by non-persisted, non-commerce proposal shapes (see
attack_lab.py). This module is the persistence adapter for the one flavor
that needs to survive a restart, not a replacement for the dataclass.

Same shared-SQLite-connection gap as ledger.py/capability.py (see
their docstrings): db.py::make_engine's one StaticPool connection for
":memory:"/file SQLite is unsafe for two real OS threads to call
commit() on at the same instant. Guarded the same way, for the same
reason.
"""

from __future__ import annotations

import threading
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, select

from .lifecycle import ActionProposal
from ..db import make_engine


_CART_PROPOSALS_LOCK = threading.Lock()

__all__ = ["cart_proposals_engine", "save_proposal", "load_proposal"]

_DEFAULT_PATH = Path("data/cart_proposals.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CartProposalRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    proposal_id: str = Field(unique=True, index=True)
    connector_id: str
    capability: str
    risk_tier: str
    status: str
    tool_name: str
    arguments_json: str
    summary: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


def cart_proposals_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    return make_engine(path)


def save_proposal(engine: Engine, proposal: ActionProposal) -> None:
    """Upsert by proposal_id — called at creation and after every status
    change, so the stored row always matches the in-process object exactly."""
    with _CART_PROPOSALS_LOCK, Session(engine) as db:
        row = db.exec(
            select(CartProposalRecord).where(CartProposalRecord.proposal_id == proposal.proposal_id)
        ).first()
        if row is None:
            row = CartProposalRecord(proposal_id=proposal.proposal_id, arguments_json="{}", tool_name="")
        row.connector_id = proposal.connector_id
        row.capability = proposal.capability
        row.risk_tier = proposal.risk_tier
        row.status = proposal.status
        row.tool_name = proposal.tool_name
        row.arguments_json = json.dumps(proposal.arguments)
        row.summary = proposal.summary
        row.updated_at = _now()
        db.add(row)
        db.commit()


def load_proposal(engine: Engine, proposal_id: str) -> ActionProposal | None:
    with _CART_PROPOSALS_LOCK, Session(engine) as db:
        row = db.exec(
            select(CartProposalRecord).where(CartProposalRecord.proposal_id == proposal_id)
        ).first()
        if row is None:
            return None
        return ActionProposal(
            proposal_id=row.proposal_id,
            connector_id=row.connector_id,
            capability=row.capability,
            risk_tier=row.risk_tier,  # type: ignore[arg-type]
            status=row.status,  # type: ignore[arg-type]
            tool_name=row.tool_name,
            arguments=json.loads(row.arguments_json),
            summary=row.summary,
        )
