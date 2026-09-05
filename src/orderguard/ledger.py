"""The idempotency ledger: at-least-once calls, exactly-once business effect.

Contract, docs/API_CONTRACTS.md #5:

    idempotency_key = merchant_id | purchase_intent_id | action_type | cart_hash

``cart_hash`` is frozen once at confirmation (D-004) and never recomputed, so
retrying the same confirmed purchase always produces the same key.

**Enforced by a database UNIQUE constraint, claimed before the store write.**
Not an application-level ``if not exists`` check — that races. Two requests
arriving together both see "not exists" and both proceed. A UNIQUE constraint
makes the database itself the referee: exactly one INSERT can ever succeed for
a given key, and SQLite serializes that decision even under concurrent access.

Two moments are claimed separately, and the difference matters:

**Claiming an order** (``claim_order``) happens before we ever call Razorpay.
A retried "create order" call must return the SAME Razorpay order, never a
second one — otherwise a flaky network retry would leave two live orders for
one purchase.

**Finalizing a payment** (``finalize_if_pending``) happens after
``verify_payment`` succeeds, and is itself guarded by an atomic
``UPDATE ... WHERE status = 'pending'``. The first call to reach this line wins
the row; every later call — whether it is a genuine retry, a duplicate webhook,
or 70 identical requests fired at once — finds the row already resolved and is
handed back the *original* result instead of writing anything.

Real, found gap (not hypothetical): claiming the LEDGER ROW and claiming the
right to actually CALL RAZORPAY were the same check for a while
(``entry.status is PENDING and not entry.razorpay_order_id``), and that check
is not atomic — two concurrent calls for the identical idempotency key can
both read "no order id yet" before either one finishes attaching it, and both
go on to call Razorpay's create-order API, producing two real orders for one
purchase. The UNIQUE constraint on ``idempotency_key`` only ever stopped a
second *row*; it says nothing about a second network call against the same
row. ``claim_order_creation`` closes this with its own atomic
``UPDATE ... WHERE order_creation_claimed_at IS NULL``, exactly the same
database-as-referee pattern as everything else here: exactly one caller's
UPDATE can match that WHERE clause, so exactly one caller may ever attempt
the Razorpay call for a given key at a time.

Real, found gap, same shape as ``capability.py``'s own documented one: every
function below shares ``db.py::make_engine``'s SQLite path, which for
``:memory:`` (tests, and any deploy with no DATABASE_URL) is one single
``StaticPool`` connection for the whole process. Proving a WHERE clause is
atomic at the SQL level does not make it safe for two real OS threads to
call ``execute()``/``commit()`` on that ONE shared connection object at the
same instant — Python's ``sqlite3`` driver raises
``sqlite3.InterfaceError: bad parameter or other API misuse`` under exactly
that load, found here the same way ``capability.py`` found it: a genuine
multi-threaded test, not a sequential one. Fixed the same way, for the same
reason: a module-level ``threading.Lock`` around every function that touches
the engine, so concurrent callers queue at the Python level instead of
colliding on the driver. Postgres (DATABASE_URL set) does not need this —
each caller gets its own pooled connection — but the lock is harmless there
too, and this module must work correctly in both configurations.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from sqlalchemy import Engine, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from .db import make_engine

# See this module's docstring: guards every function below against genuine
# concurrent access to the one shared SQLite connection object a `:memory:`
# or file-based StaticPool engine hands out.
_LEDGER_LOCK = threading.Lock()

__all__ = [
    "LedgerStatus", "LedgerEntry", "ledger_engine",
    "claim_order", "claim_order_creation", "finalize_if_pending", "reject",
    "get_entry", "get_entry_by_order_id", "mark_unknown", "resolve_unknown",
]

_DEFAULT_PATH = Path("data/ledger.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LedgerStatus(StrEnum):
    PENDING = "pending"      # order claimed, no verified payment yet
    CAPTURED = "captured"    # terminal. Money moved, exactly once.
    REJECTED = "rejected"    # a verification attempt failed; not terminal —
                             # a later attempt with the correct proof may still
                             # succeed, because rejecting one bad claim must not
                             # burn the user's only chance to pay correctly
    UNKNOWN = "unknown"      # the create_order call's OUTCOME is uncertain — a
                             # timeout or dropped connection means we cannot
                             # tell "Razorpay never got it" from "Razorpay made
                             # the order and the response got lost". Never
                             # treated as FAILED (that would license a blind
                             # retry that could create a second real order) and
                             # never treated as CAPTURED. The only way out is
                             # asking Razorpay directly — see
                             # app.py::_resolve_unknown_order, which calls
                             # executor.find_order_by_receipt.


class LedgerEntry(SQLModel, table=True):
    """One row per purchase attempt. The UNIQUE constraint is the whole point."""

    id: int | None = Field(default=None, primary_key=True)
    idempotency_key: str = Field(unique=True, index=True)

    merchant: str
    purchase_intent_id: str
    cart_hash: str
    expected_amount_paise: int
    currency: str

    status: LedgerStatus = LedgerStatus.PENDING
    razorpay_order_id: str = ""
    razorpay_payment_id: str = ""
    captured_amount_paise: int | None = None
    last_rejection_reason: str = ""

    # Set exactly once, atomically, by whichever caller wins the right to
    # actually call Razorpay's create-order API for this key — see
    # claim_order_creation. Separate from razorpay_order_id: that field is
    # only set once the call SUCCEEDS, but the claim must be held for the
    # whole in-flight window, including the network round trip, or a second
    # caller reading "no order id yet" would call Razorpay too.
    order_creation_claimed_at: datetime | None = None

    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None


def ledger_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    return make_engine(path)


def claim_order(
    engine: Engine,
    *,
    idempotency_key: str,
    merchant: str,
    purchase_intent_id: str,
    cart_hash: str,
    expected_amount_paise: int,
    currency: str,
) -> tuple[LedgerEntry, bool]:
    """Claim this purchase, or hand back the existing claim.

    Returns ``(entry, created)``. ``created`` is True only for the caller that
    actually won the INSERT — that caller, and only that caller, should go on
    to call Razorpay's create-order API. Every other caller gets the row that
    already exists, including any ``razorpay_order_id`` already stored on it,
    and must not create a second order.
    """
    with _LEDGER_LOCK, Session(engine) as db:
        entry = LedgerEntry(
            idempotency_key=idempotency_key, merchant=merchant,
            purchase_intent_id=purchase_intent_id, cart_hash=cart_hash,
            expected_amount_paise=expected_amount_paise, currency=currency,
        )
        db.add(entry)
        try:
            db.commit()
        except IntegrityError:
            # Someone else's INSERT won the UNIQUE constraint first. This is
            # not an error condition — it is the mechanism working.
            db.rollback()
            existing = db.exec(
                select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
            ).one()
            return existing, False
        db.refresh(entry)
        return entry, True


def attach_order(engine: Engine, idempotency_key: str, razorpay_order_id: str) -> None:
    """Record the Razorpay order id on the row the winning caller just claimed."""
    with _LEDGER_LOCK, Session(engine) as db:
        db.exec(
            update(LedgerEntry)
            .where(LedgerEntry.idempotency_key == idempotency_key)
            .values(razorpay_order_id=razorpay_order_id)
        )
        db.commit()


def claim_order_creation(engine: Engine, idempotency_key: str) -> bool:
    """True for exactly one caller among any number racing here for the same
    key — that caller, and only that caller, may call Razorpay's create-order
    API. Every other caller must not call it; it should instead wait for (or
    read back) the winner's result.

    Atomic for the same reason ``finalize_if_pending`` is: the WHERE clause,
    not an application-level read-then-write, decides who wins.
    """
    with _LEDGER_LOCK, Session(engine) as db:
        result = db.exec(
            update(LedgerEntry)
            .where(
                LedgerEntry.idempotency_key == idempotency_key,
                LedgerEntry.order_creation_claimed_at.is_(None),
            )
            .values(order_creation_claimed_at=_now())
        )
        db.commit()
        return result.rowcount == 1


def finalize_if_pending(
    engine: Engine,
    *,
    idempotency_key: str,
    razorpay_payment_id: str,
    captured_amount_paise: int,
) -> tuple[LedgerEntry, bool]:
    """Mark this purchase paid — but only the first call to arrive here.

    Returns ``(entry, first_time)``. ``first_time`` is True only for the single
    caller whose UPDATE actually flips the row from pending to captured — that
    caller, and only that caller, should go on to write order history. Every
    later call, no matter how many times it is retried or replayed, finds the
    row already captured and gets back the *original* result untouched.

    The atomicity is the ``WHERE status = 'pending'`` clause. Even if two
    requests reach this function at what looks like the same instant, SQLite
    executes writes one at a time, so exactly one UPDATE statement can match
    that WHERE clause before the status changes under it.
    """
    with _LEDGER_LOCK, Session(engine) as db:
        result = db.exec(
            update(LedgerEntry)
            .where(
                LedgerEntry.idempotency_key == idempotency_key,
                LedgerEntry.status == LedgerStatus.PENDING,
            )
            .values(
                status=LedgerStatus.CAPTURED,
                razorpay_payment_id=razorpay_payment_id,
                captured_amount_paise=captured_amount_paise,
                resolved_at=_now(),
            )
        )
        db.commit()
        won = result.rowcount == 1

        # first(), not one(): a key that was never claimed has no row at all,
        # and that is a valid call shape to handle rather than an error to
        # raise — see test_finalize_before_any_claim_captures_nothing.
        entry = db.exec(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
        ).first()
        return entry, won


def reject(engine: Engine, idempotency_key: str, reason: str) -> None:
    """Record why a verification attempt failed. Leaves the row PENDING.

    Not terminal on purpose: rejecting one bad or premature claim must not
    prevent a later attempt, with the correct payment id and signature, from
    still succeeding.
    """
    with _LEDGER_LOCK, Session(engine) as db:
        db.exec(
            update(LedgerEntry)
            .where(
                LedgerEntry.idempotency_key == idempotency_key,
                LedgerEntry.status == LedgerStatus.PENDING,
            )
            .values(last_rejection_reason=reason)
        )
        db.commit()


def get_entry(engine: Engine, idempotency_key: str) -> LedgerEntry | None:
    with _LEDGER_LOCK, Session(engine) as db:
        return db.exec(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
        ).first()


def get_entry_by_order_id(engine: Engine, razorpay_order_id: str) -> LedgerEntry | None:
    """The lookup a webhook needs: it knows Razorpay's order id, not our
    idempotency key."""
    with _LEDGER_LOCK, Session(engine) as db:
        return db.exec(
            select(LedgerEntry).where(LedgerEntry.razorpay_order_id == razorpay_order_id)
        ).first()


def mark_unknown(engine: Engine, idempotency_key: str) -> None:
    """The create_order call's outcome is uncertain — never call this after a
    clean success or a clean 4xx refusal, only after a timeout or dropped
    connection where Razorpay's own state is genuinely unknown to us.
    """
    with _LEDGER_LOCK, Session(engine) as db:
        db.exec(
            update(LedgerEntry)
            .where(
                LedgerEntry.idempotency_key == idempotency_key,
                LedgerEntry.status == LedgerStatus.PENDING,
            )
            .values(status=LedgerStatus.UNKNOWN)
        )
        db.commit()


def resolve_unknown(
    engine: Engine, idempotency_key: str, *, razorpay_order_id: str | None,
) -> None:
    """Razorpay's own record is the only thing allowed to resolve an UNKNOWN
    row. ``razorpay_order_id`` given means the order WAS created despite the
    lost response — attach it and return to PENDING, the same state a normal
    successful create_order call leaves behind. ``None`` means Razorpay has no
    record of it — also PENDING, and the earlier order-creation claim is
    released too, so a genuine retry is free to attempt the Razorpay call
    again instead of being permanently locked out by a claim now known to
    have failed.
    """
    with _LEDGER_LOCK, Session(engine) as db:
        values: dict = {"status": LedgerStatus.PENDING}
        if razorpay_order_id:
            values["razorpay_order_id"] = razorpay_order_id
        else:
            values["order_creation_claimed_at"] = None
        db.exec(
            update(LedgerEntry)
            .where(
                LedgerEntry.idempotency_key == idempotency_key,
                LedgerEntry.status == LedgerStatus.UNKNOWN,
            )
            .values(**values)
        )
        db.commit()
