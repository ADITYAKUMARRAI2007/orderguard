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
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from sqlalchemy import Engine, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, select

from .db import make_engine

__all__ = [
    "LedgerStatus", "LedgerEntry", "ledger_engine",
    "claim_order", "finalize_if_pending", "reject", "get_entry",
    "get_entry_by_order_id", "mark_unknown", "resolve_unknown",
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
                             # asking Razorpay directly — see reconcile.py.


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
    with Session(engine) as db:
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
    with Session(engine) as db:
        db.exec(
            update(LedgerEntry)
            .where(LedgerEntry.idempotency_key == idempotency_key)
            .values(razorpay_order_id=razorpay_order_id)
        )
        db.commit()


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
    with Session(engine) as db:
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
    with Session(engine) as db:
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
    with Session(engine) as db:
        return db.exec(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
        ).first()


def get_entry_by_order_id(engine: Engine, razorpay_order_id: str) -> LedgerEntry | None:
    """The lookup a webhook needs: it knows Razorpay's order id, not our
    idempotency key."""
    with Session(engine) as db:
        return db.exec(
            select(LedgerEntry).where(LedgerEntry.razorpay_order_id == razorpay_order_id)
        ).first()


def mark_unknown(engine: Engine, idempotency_key: str) -> None:
    """The create_order call's outcome is uncertain — never call this after a
    clean success or a clean 4xx refusal, only after a timeout or dropped
    connection where Razorpay's own state is genuinely unknown to us.
    """
    with Session(engine) as db:
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
    record of it — also PENDING, but with no order attached, so the next
    payment/order call is free to actually create one.
    """
    with Session(engine) as db:
        values: dict = {"status": LedgerStatus.PENDING}
        if razorpay_order_id:
            values["razorpay_order_id"] = razorpay_order_id
        db.exec(
            update(LedgerEntry)
            .where(
                LedgerEntry.idempotency_key == idempotency_key,
                LedgerEntry.status == LedgerStatus.UNKNOWN,
            )
            .values(**values)
        )
        db.commit()
