"""One shared place that decides SQLite (local dev, tests, and any deploy
with no DATABASE_URL) vs. Postgres (DATABASE_URL set) for every module in
this codebase that owns its own SQL table(s).

Why this exists: every one of audit.py / authorization.py / capability.py /
ledger.py / memory.py / webhooks.py / connector_log.py /
agent/connector_accounts.py / agent/custom_connectors.py used to build its
own ``create_engine(...)`` call, each identical except for the default file
path. On Render's free tier that file lives on an ephemeral filesystem --
wiped on every redeploy, which silently deleted real connector OAuth
tokens a user had just created (FAILURE_LOG.md F-035). Free persistent
disks are not available on Render's free compute plan at all, and every
comparable "just switch platforms" alternative checked (Fly.io, Railway,
Koyeb) turned out to have dropped free persistent volume storage too, as of
this project's build window -- a free, always-on hosted Postgres (Neon,
Supabase) was the option that didn't cost money or trade away durability.

Every ``<name>_engine()`` function keeps its existing signature and default
SQLite path -- local dev and the test suite (which never sets DATABASE_URL)
are completely unaffected. Set DATABASE_URL and every module's data lands
in the SAME Postgres database instead, which is safe: they already share
one global ``SQLModel.metadata``, so each module's own ``create_all`` call
only ever adds the tables that do not yet exist.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

__all__ = ["make_engine"]


def _normalized_database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    # Neon, Supabase and most managed providers hand out a bare
    # "postgres://" or "postgresql://" connection string. SQLAlchemy 1.4+
    # requires "postgresql://" (not "postgres://"), and with no driver
    # suffix it defaults to psycopg2, which this project does not install --
    # psycopg (v3) is the dependency, so the URL must say so explicitly.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def make_engine(path: Path | str = ":memory:") -> Engine:
    """Postgres when DATABASE_URL is set; otherwise the caller's own SQLite
    file, or an in-memory database for ``path == ":memory:"`` (tests, and
    any caller that wants a fresh, isolated database).
    """
    database_url = _normalized_database_url()
    if database_url:
        # pool_pre_ping: a free-tier serverless Postgres (Neon) suspends
        # itself on idle and wakes on the next connection -- without this,
        # the first query after a suspend reuses a dead pooled connection
        # and fails instead of transparently reconnecting.
        engine = create_engine(database_url, echo=False, pool_pre_ping=True)
    elif path == ":memory:":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool, echo=False,
        )
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}, echo=False,
        )
    SQLModel.metadata.create_all(engine)
    return engine
