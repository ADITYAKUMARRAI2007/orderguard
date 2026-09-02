"""Execution Capability v1: the single-use, atomically-consumed authorization
a money-moving call must present before ``executor.py`` will touch Razorpay.

Where this sits relative to what already existed:

- **Signed Authorization** (``authorization.py``) is the artifact handed back
  to the *caller* — independently re-verifiable from its own bytes, evidence
  a judge or a browser can check.
- **The idempotency ledger** (``ledger.py``) is what guarantees at-most-once
  business effect for a given purchase, keyed by merchant/intent/cart hash.
- **This module** is neither of those. It is the internal, server-side gate
  ``executor.py`` itself requires before it will act at all — mint one right
  after ``evaluate_pre_payment_gates`` passes, bind it to the EXACT amount,
  currency, merchant and receipt the gates just verified, and the executor
  loads those values FROM the stored capability rather than trusting
  whatever a caller passes alongside it. A caller cannot say "use this
  capability, but charge ₹24,999 instead" — the capability does not accept
  an amount parameter to override; it only ever authorizes the amount it
  was minted with.

Atomicity mirrors ``ledger.py::finalize_if_pending`` exactly, on purpose:
an ``UPDATE ... WHERE consumed_at IS NULL AND expires_at > now()`` is what
lets the database itself be the referee under real concurrent access,
instead of an application-level check-then-set that races. See
``consume_capability``'s own docstring and
``tests/test_capability.py``'s 50-way concurrent consumption test for the
proof.

**A real blocker found while proving that, reported rather than hidden**:
the 50-way concurrency test originally used genuine OS threads
(``asyncio.to_thread``) hitting the shared SQLite connection at once, and
it did not just fail the security property — it raised
``sqlite3.InterfaceError: bad parameter or other API misuse``. That is a
different failure than a race in the WHERE clause: Python's ``sqlite3``
driver is not safe for two threads to call ``execute()``/``commit()`` on
the SAME connection object at literally the same instant, even with
``check_same_thread=False`` (that flag only lifts the same-thread
restriction; it does not make concurrent use of one connection object
safe). The SQL-level atomicity of the UPDATE itself was never in question
— the driver-level access to the shared connection was. Fixed with the
smallest change that provides genuine thread-safety: a module-level
``threading.Lock`` held around the read-modify-read sequence in
``consume_capability``, so concurrent threads queue at the Python level
instead of colliding on the driver. This does not weaken the property
being proven — SQLite was always going to serialize the actual disk writes
one at a time regardless; the lock only stops the Python driver from being
handed two simultaneous calls it was never safe to accept.

Honest limit, stated once here rather than left implicit: this proves "no
execution happens without a valid, unexpired, single-use, gate-issued
capability" — the SOURCE-PATH claim. It does not (and cannot, from Python
alone) prove that a fully compromised process with arbitrary code execution
inside it couldn't still act by calling this module's own functions
directly with attacker-chosen capability parameters at issuance time. That
stronger claim needs the capability ISSUER itself to be reachable only from
the deterministic, gate-passed code path — enforced today by
``tests/test_architecture_boundaries.py`` restricting who may import this
module at all (agent/ and mcp_server.py cannot), not by anything
cryptographic. A real production boundary would run the executor as a
genuinely separate process/service that the web process cannot introspect;
this module does not attempt that yet — see ``executor.py``'s own docstring.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Engine, update
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

__all__ = [
    "ExecutionCapability", "capability_engine",
    "issue_capability", "consume_capability",
    "CAPABILITY_NOT_FOUND", "CAPABILITY_EXPIRED", "CAPABILITY_ALREADY_CONSUMED",
]

_DEFAULT_PATH = Path("data/capabilities.db")

# Minted right before the executor call it authorizes and consumed a few
# lines later in the same request -- this is not the "how long may a user
# take to complete Razorpay Checkout" window (that is
# checkout_guard.DEFAULT_AUTHORIZATION_TTL, unrelated and unchanged by this
# module). A short window is deliberate: the shorter it is, the less time
# a capability sits around as something that could theoretically be reused.
DEFAULT_CAPABILITY_TTL = timedelta(seconds=60)

CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
CAPABILITY_EXPIRED = "CAPABILITY_EXPIRED"
CAPABILITY_ALREADY_CONSUMED = "CAPABILITY_ALREADY_CONSUMED"


def _now() -> datetime:
    # Naive, not aware -- deliberately. SQLite (via SQLModel's default
    # DateTime column) strips tzinfo on round-trip through storage; a value
    # read back from a SELECT is naive even if what was originally INSERTed
    # was timezone-aware. Comparing a freshly-computed aware datetime
    # against that read-back value raises TypeError ("can't compare
    # offset-naive and offset-aware datetimes") -- found by the expiry test
    # actually failing, not assumed upfront. Every datetime this module
    # stores or compares is UTC by convention (never local time), so
    # dropping tzinfo consistently on both sides is safe and avoids the
    # mismatch entirely, rather than special-casing every read-back site.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ExecutionCapability(SQLModel, table=True):
    """One row per minted capability. ``consumed_at`` is the whole point —
    see ``consume_capability``."""

    id: int | None = Field(default=None, primary_key=True)
    capability_id: str = Field(unique=True, index=True)

    # What this capability authorizes -- and ALL the executor will ever act
    # on, once consumption succeeds. A caller supplying a different amount
    # alongside a valid capability_id has no effect: these stored values are
    # the only source of truth for the execution that follows.
    session_id: str
    operation: str
    merchant: str
    amount_paise: int
    currency: str
    receipt: str
    cart_hash: str = ""
    nonce: str

    issued_at: datetime = Field(default_factory=_now)
    expires_at: datetime
    consumed_at: datetime | None = None


def capability_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """Same shape as ledger.ledger_engine — see its docstring for why
    in-memory SQLite needs StaticPool and check_same_thread=False. The
    latter matters here specifically: the concurrent-consumption test drives
    this engine from multiple OS threads at once via asyncio.to_thread, and
    a single shared SQLite connection must accept that."""
    if path == ":memory:":
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


def issue_capability(
    engine: Engine,
    *,
    session_id: str,
    operation: str,
    merchant: str,
    amount_paise: int,
    currency: str,
    receipt: str,
    cart_hash: str = "",
    ttl: timedelta = DEFAULT_CAPABILITY_TTL,
) -> ExecutionCapability:
    """Mint a new capability. Callers of THIS function are the trust
    boundary — see this module's own docstring and
    tests/test_architecture_boundaries.py for who may reach it at all.
    Nothing here checks that gates already passed; that is the caller's
    job (app.py, only after evaluate_pre_payment_gates.allow is True).
    """
    with Session(engine) as db:
        cap = ExecutionCapability(
            capability_id=f"cap_{uuid.uuid4().hex}",
            session_id=session_id, operation=operation, merchant=merchant,
            amount_paise=amount_paise, currency=currency, receipt=receipt,
            cart_hash=cart_hash, nonce=uuid.uuid4().hex,
            expires_at=_now() + ttl,
        )
        db.add(cap)
        db.commit()
        db.refresh(cap)
        return cap


# Guards the read-modify-read sequence below against genuine, simultaneous
# multi-threaded access to one shared SQLite connection -- see this
# module's own docstring for the real InterfaceError this was found to
# produce without it. Does not weaken the atomicity property being
# enforced: the WHERE clause is still what decides who wins; this lock
# only stops the Python driver from being handed two overlapping calls it
# was never safe to accept on a single connection object.
_CONSUME_LOCK = threading.Lock()


def consume_capability(
    engine: Engine, capability_id: str,
) -> tuple[ExecutionCapability | None, str]:
    """Atomically consume a capability. Returns ``(capability, "")`` on the
    single call that wins, or ``(None, reason)`` for every other call —
    including every one of 49 concurrent losers in a replay attempt.

    The atomicity is the ``WHERE consumed_at IS NULL AND expires_at > now``
    clause, exactly ``ledger.py::finalize_if_pending``'s own pattern: SQLite
    executes writes one at a time, so exactly one UPDATE statement can match
    that WHERE clause before consumed_at changes under it, no matter how
    many callers race to get here at once.
    """
    now = _now()
    with _CONSUME_LOCK, Session(engine) as db:
        result = db.exec(
            update(ExecutionCapability)
            .where(
                ExecutionCapability.capability_id == capability_id,
                ExecutionCapability.consumed_at.is_(None),
                ExecutionCapability.expires_at > now,
            )
            .values(consumed_at=now)
        )
        db.commit()
        row = db.exec(
            select(ExecutionCapability).where(ExecutionCapability.capability_id == capability_id)
        ).first()

        if result.rowcount == 1:
            return row, ""
        if row is None:
            return None, CAPABILITY_NOT_FOUND
        if row.expires_at <= now:
            return None, CAPABILITY_EXPIRED
        return None, CAPABILITY_ALREADY_CONSUMED
