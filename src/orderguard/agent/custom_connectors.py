"""User-added remote MCP connectors. Every URL goes through ``ssrf_guard``
before it's ever contacted — at registration AND at every subsequent call,
re-resolved each time, to close the DNS-rebinding gap a one-time check at
registration would leave open.

Tools discovered via a real ``tools/list`` call are stored **disabled**.
Populating the catalog is never the same as making a tool usable — each one
needs one explicit enable plus a risk-tier assignment before
``connector_registry``/``eligibility`` will ever consider it eligible for
routing. This is the opposite default from "list it, then trust it," on
purpose.

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

import httpx
from sqlalchemy import Engine
from sqlmodel import Field, Session, SQLModel, select

from ..db import make_engine
from .ssrf_guard import assert_no_cross_host_redirect, assert_safe_url
from .tools import ToolPermission


_CUSTOM_CONNECTORS_LOCK = threading.Lock()

__all__ = [
    "CustomConnector", "CustomConnectorTool", "custom_connectors_engine",
    "CustomConnectorProtocolError", "register_custom_connector",
    "discover_tools", "enable_tool", "enabled_tools",
]

_DEFAULT_PATH = Path("data/custom_connectors.db")
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CustomConnector(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_ref: str = Field(default="local", index=True)
    label: str
    url: str
    category: str = "CUSTOM"
    created_at: datetime = Field(default_factory=_now)


class CustomConnectorTool(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    connector_id: int = Field(index=True)
    name: str
    enabled: bool = False
    risk_tier: str | None = None  # set only when a human enables it
    capability: str | None = None


class CustomConnectorProtocolError(RuntimeError):
    """The remote server did not return a valid MCP tools/list response."""


def custom_connectors_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    engine = make_engine(path)
    # SQLite-only: PRAGMA has no Postgres equivalent, and doesn't need one —
    # create_all (inside make_engine) already gives a fresh Postgres table
    # every current column. This only patches an OLD, already-created
    # SQLite file from before "capability" existed on the model.
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            columns = {
                row[1] for row in connection.exec_driver_sql(
                    "PRAGMA table_info(customconnectortool)"
                ).fetchall()
            }
            if "capability" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE customconnectortool ADD COLUMN capability VARCHAR"
                )
    return engine


def register_custom_connector(engine: Engine, *, label: str, url: str, category: str = "CUSTOM") -> CustomConnector:
    assert_safe_url(url)
    with _CUSTOM_CONNECTORS_LOCK, Session(engine) as db:
        row = CustomConnector(label=label, url=url, category=category)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


async def discover_tools(
    engine: Engine, connector_id: int, *, client: httpx.AsyncClient | None = None
) -> list[str]:
    """One real ``tools/list`` call. Every discovered tool is inserted
    disabled — see module docstring. ``client`` is injectable so tests can
    use ``httpx.MockTransport`` instead of a real network call, matching
    ``commerce/shopify_mcp.py``'s existing test pattern.
    """
    with _CUSTOM_CONNECTORS_LOCK, Session(engine) as db:
        connector = db.get(CustomConnector, connector_id)
        if connector is None:
            raise KeyError(f"unknown custom connector id {connector_id}")
        url = connector.url

    assert_safe_url(url)
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    try:
        try:
            resp = await client.post(
                url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"custom connector tools/list failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if resp.is_redirect:
        assert_no_cross_host_redirect(url, str(resp.headers.get("location", url)))

    if resp.status_code != 200:
        raise CustomConnectorProtocolError(
            f"custom connector tools/list returned HTTP {resp.status_code}"
        )

    try:
        body = resp.json()
        tools = body["result"]["tools"]
        if not isinstance(tools, list):
            raise TypeError("result.tools was not a list")
        names = []
        for tool in tools:
            if not isinstance(tool, dict):
                raise TypeError("tool entry was not an object")
            name = tool.get("name")
            if not isinstance(name, str) or not name.strip():
                raise TypeError("tool entry had no non-empty name")
            names.append(name)
        if len(set(names)) != len(names):
            raise TypeError("tools/list returned duplicate tool names")
    except (ValueError, KeyError, TypeError) as exc:
        raise CustomConnectorProtocolError(
            f"custom connector returned malformed tools/list: {exc}"
        ) from None

    with _CUSTOM_CONNECTORS_LOCK, Session(engine) as db:
        for name in names:
            exists = db.exec(
                select(CustomConnectorTool).where(
                    CustomConnectorTool.connector_id == connector_id,
                    CustomConnectorTool.name == name,
                )
            ).first()
            if exists is None:
                db.add(CustomConnectorTool(connector_id=connector_id, name=name, enabled=False))
        db.commit()
    return names


def enable_tool(
    engine: Engine, connector_id: int, tool_name: str,
    risk_tier: str, capability: str,
) -> None:
    if risk_tier == "R3":
        raise ValueError("a custom connector tool may never be enabled at risk tier R3")
    with _CUSTOM_CONNECTORS_LOCK, Session(engine) as db:
        row = db.exec(
            select(CustomConnectorTool).where(
                CustomConnectorTool.connector_id == connector_id,
                CustomConnectorTool.name == tool_name,
            )
        ).first()
        if row is None:
            raise KeyError(f"tool {tool_name!r} was never discovered on connector {connector_id}")
        row.enabled = True
        row.risk_tier = risk_tier
        row.capability = capability
        db.add(row)
        db.commit()


def enabled_tools(engine: Engine, connector_id: int) -> tuple[ToolPermission, ...]:
    with _CUSTOM_CONNECTORS_LOCK, Session(engine) as db:
        rows = db.exec(
            select(CustomConnectorTool).where(
                CustomConnectorTool.connector_id == connector_id,
                CustomConnectorTool.enabled == True,  # noqa: E712
            )
        ).all()
    mutation = {
        "R0": "READ", "R1": "REVERSIBLE_WRITE",
        "R2": "EXTERNAL_COMMITMENT", "R3": "FINANCIAL",
    }
    return tuple(
        ToolPermission(
            r.name, r.risk_tier or "R1",
            mutation.get(r.risk_tier or "R1", "REVERSIBLE_WRITE"),
        )
        for r in rows
    )
