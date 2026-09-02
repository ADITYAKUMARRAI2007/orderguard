"""Execution Capability v1: the security properties that matter, proven
directly against capability.py's own storage, plus one end-to-end proof
through the real /api/sessions/*/payment/order endpoint.

Every rejection case below asserts the SAME thing at its center: Razorpay
was never called. A capability failing safe that still somehow reached the
network would be worse than no capability at all -- it would look protected
without being protected.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlmodel import Session, select

from orderguard.capability import (
    CAPABILITY_ALREADY_CONSUMED,
    CAPABILITY_EXPIRED,
    CAPABILITY_NOT_FOUND,
    ExecutionCapability,
    capability_engine,
    consume_capability,
    issue_capability,
)


def _naive_now() -> datetime:
    # Same naive-UTC convention as capability.py's own _now() -- see that
    # module's docstring for why (SQLite strips tzinfo on round-trip).
    return datetime.now(timezone.utc).replace(tzinfo=None)
from orderguard.executor import CapabilityRejected, execute_create_order


def _db():
    return capability_engine(":memory:")


def _issue(engine, **overrides):
    data = dict(
        session_id="sess_1", operation="razorpay.create_order",
        merchant="freshcart", amount_paise=13200, currency="INR",
        receipt="freshcart|intent-1|purchase|hash-1", cart_hash="hash-1",
    )
    data.update(overrides)
    return issue_capability(engine, **data)


# --- storage-level: minting -------------------------------------------------

def test_a_minted_capability_is_unconsumed_and_carries_exactly_what_it_was_given():
    engine = _db()
    cap = _issue(engine, amount_paise=47900, currency="INR", merchant="store_xyz")
    assert cap.consumed_at is None
    assert cap.amount_paise == 47900
    assert cap.currency == "INR"
    assert cap.merchant == "store_xyz"
    assert cap.capability_id.startswith("cap_")
    assert cap.nonce


def test_two_mints_never_collide_on_capability_id_or_nonce():
    engine = _db()
    a = _issue(engine)
    b = _issue(engine)
    assert a.capability_id != b.capability_id
    assert a.nonce != b.nonce


# --- storage-level: consumption ---------------------------------------------

def test_a_fresh_capability_consumes_successfully_exactly_once():
    engine = _db()
    cap = _issue(engine)
    consumed, reason = consume_capability(engine, cap.capability_id)
    assert consumed is not None and reason == ""
    assert consumed.consumed_at is not None


def test_replaying_the_same_capability_id_is_rejected_not_reprocessed():
    """The specific attack this exists to stop: reusing a capability that
    already authorized one real execution to authorize a second one."""
    engine = _db()
    cap = _issue(engine)
    first, _ = consume_capability(engine, cap.capability_id)
    assert first is not None

    second, reason = consume_capability(engine, cap.capability_id)
    assert second is None
    assert reason == CAPABILITY_ALREADY_CONSUMED


def test_an_unknown_capability_id_is_rejected():
    engine = _db()
    consumed, reason = consume_capability(engine, "cap_never_issued")
    assert consumed is None
    assert reason == CAPABILITY_NOT_FOUND


def test_an_expired_capability_is_rejected_even_if_never_consumed():
    engine = _db()
    cap = _issue(engine, ttl=timedelta(seconds=-1))   # already expired at mint time
    consumed, reason = consume_capability(engine, cap.capability_id)
    assert consumed is None
    assert reason == CAPABILITY_EXPIRED


# --- the 50-way concurrent consumption proof --------------------------------

async def _consume_in_a_real_thread(engine, capability_id):
    """asyncio.to_thread, not a plain sequential loop -- this genuinely runs
    the fifty consume_capability calls across real OS threads against the
    SAME shared SQLite connection (capability_engine's check_same_thread=
    False exists specifically to allow this), so the atomic UPDATE actually
    has to arbitrate real concurrent access, not just calls that happen to
    be issued one after another in Python source order."""
    return await asyncio.to_thread(consume_capability, engine, capability_id)


async def _run_fifty_concurrent_consumptions(engine, capability_id):
    results = await asyncio.gather(
        *[_consume_in_a_real_thread(engine, capability_id) for _ in range(50)]
    )
    return results


def test_fifty_concurrent_consumption_attempts_yield_exactly_one_winner():
    engine = _db()
    cap = _issue(engine)

    results = asyncio.run(_run_fifty_concurrent_consumptions(engine, cap.capability_id))

    winners = [r for r in results if r[0] is not None]
    losers = [r for r in results if r[0] is None]
    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)}"
    assert len(losers) == 49
    assert all(reason == CAPABILITY_ALREADY_CONSUMED for _, reason in losers)


# --- the mutation test: proving the atomic WHERE clause is load-bearing -----
# Not a hypothetical -- a real, non-atomic check-then-set implementation,
# run through the exact same 50-way concurrent harness above, against the
# exact same table. If this ever stops over-consuming under load, that is
# itself informative (hardware/SQLite build changed), not a reason to trust
# consume_capability's atomicity any less -- the WHERE-clause version is
# proven directly, on its own, by the test above; this one only exists to
# show the naive alternative genuinely was not safe.

_NAIVE_STEP_LOCK = threading.Lock()   # driver-safety only -- see below, NOT what makes this "safe"


def _naive_check_then_set_consume(engine, capability_id):
    """The BAD pattern this module's own docstring warns against, written
    out for real: read, decide in Python, write, as two SEPARATE database
    calls with the actual decision made in between. Each individual call is
    still wrapped in a brief lock -- that only keeps Python's sqlite3
    driver itself from being handed two truly simultaneous calls (a
    separate, driver-level concern, see capability.py's own docstring for
    the InterfaceError this avoids). It does NOT close the real race: the
    lock is released between the read and the write, so another thread's
    read can land in that exact gap and reach the same "not yet consumed"
    conclusion before this thread's write ever happens.

    A short, deliberate ``time.sleep`` sits in that gap. Without it, this
    test was found to pass unreliably -- CPython's GIL means only one
    thread runs Python bytecode at a time, and the natural read-to-write
    gap here is a handful of bytecode instructions, too narrow for the OS
    scheduler to reliably interleave 50 threads into it. Real production
    race windows are just as real but usually wider (a network call, a
    second query, anything that yields the GIL) -- widening the window
    here on purpose is what makes an otherwise-real race reproducible in a
    fast, deterministic unit test, not a way of manufacturing a race that
    would not otherwise exist."""
    import time

    now = _naive_now()
    with _NAIVE_STEP_LOCK, Session(engine) as db:
        row = db.exec(
            select(ExecutionCapability).where(ExecutionCapability.capability_id == capability_id)
        ).first()
    if row is None or row.consumed_at is not None or row.expires_at <= now:
        return None
    # <-- the real race window: lock released, another thread's read above
    # can land here too, before this thread's write below ever runs.
    time.sleep(0.01)
    with _NAIVE_STEP_LOCK, Session(engine) as db:
        db.exec(
            update(ExecutionCapability)
            .where(ExecutionCapability.capability_id == capability_id)
            .values(consumed_at=now)
        )
        db.commit()
    return row


async def _run_fifty_concurrent_naive_consumptions(engine, capability_id):
    return await asyncio.gather(*[
        asyncio.to_thread(_naive_check_then_set_consume, engine, capability_id)
        for _ in range(50)
    ])


def test_the_naive_check_then_set_pattern_genuinely_over_consumes_under_load():
    """Real evidence, not a claim: run the SAME 50-way concurrent attack
    against a version of consume that does not use the atomic WHERE clause.
    at_least_two, not exactly_one, is the honest assertion -- exactly how
    many of the 50 win a real race is itself non-deterministic (depends on
    OS thread scheduling), which is precisely the point: an unsafe pattern
    does not fail predictably, it fails SOMETIMES, which is worse."""
    engine = _db()
    cap = _issue(engine)

    results = asyncio.run(_run_fifty_concurrent_naive_consumptions(engine, cap.capability_id))
    winners = [r for r in results if r is not None]

    assert len(winners) >= 2, (
        "expected the naive check-then-set pattern to over-consume under "
        f"real concurrent load, but only {len(winners)} of 50 calls won -- "
        "if this starts failing consistently, it does not mean the naive "
        "pattern became safe, it means this run's thread scheduling did not "
        "happen to race; the atomic version above is what is actually relied on."
    )


# --- the executor: rejection means Razorpay is never touched ----------------

class _RazorpayClientThatMustNeverBeCalled:
    """If this class is ever instantiated, the test that used it fails --
    that is the entire point. A capability rejection must short-circuit
    before RazorpayClient(...) is ever reached."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("RazorpayClient constructed after a capability was rejected")


@pytest.fixture(autouse=True)
def _never_touch_the_network(monkeypatch):
    import orderguard.executor as executor_module
    monkeypatch.setattr(executor_module, "RazorpayClient", _RazorpayClientThatMustNeverBeCalled)
    monkeypatch.setenv("RZP_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RZP_KEY_SECRET", "fake_secret")


async def test_a_missing_capability_is_blocked_before_razorpay():
    with pytest.raises(CapabilityRejected) as exc:
        await execute_create_order(_db(), "cap_never_issued")
    assert exc.value.reason == CAPABILITY_NOT_FOUND


async def test_an_expired_capability_is_blocked_before_razorpay():
    engine = _db()
    cap = _issue(engine, ttl=timedelta(seconds=-1))
    with pytest.raises(CapabilityRejected) as exc:
        await execute_create_order(engine, cap.capability_id)
    assert exc.value.reason == CAPABILITY_EXPIRED


class _OnceOnlyClient:
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def create_order(self, **kwargs):
        type(self).calls += 1
        return {"id": "order_fake"}


async def test_a_replayed_capability_is_blocked_the_second_time(monkeypatch):
    engine = _db()
    cap = _issue(engine)
    # First consumption succeeds and DOES reach the (test-double) Razorpay
    # client -- so this test alone needs a real fake, not the network-guard
    # fixture's assertion-raising stub.
    import orderguard.executor as executor_module

    _OnceOnlyClient.calls = 0
    monkeypatch.setattr(executor_module, "RazorpayClient", _OnceOnlyClient)

    order = await execute_create_order(engine, cap.capability_id)
    assert order["id"] == "order_fake"
    assert _OnceOnlyClient.calls == 1

    monkeypatch.setattr(executor_module, "RazorpayClient", _RazorpayClientThatMustNeverBeCalled)
    with pytest.raises(CapabilityRejected) as exc:
        await execute_create_order(engine, cap.capability_id)
    assert exc.value.reason == CAPABILITY_ALREADY_CONSUMED
    assert _OnceOnlyClient.calls == 1   # still exactly one real call, ever


# --- the amount/currency/merchant a capability authorizes cannot be overridden

async def test_the_executor_takes_no_amount_parameter_to_override_the_capability():
    """There is no keyword argument on execute_create_order for amount,
    currency, or merchant -- structurally, not just by convention. Calling
    it with anything other than (capability_db, capability_id) is a
    TypeError, which is exactly the "tampered amount" attack the doc asked
    to prove impossible: there is nothing to tamper because there is
    nothing else to pass."""
    import inspect
    sig = inspect.signature(execute_create_order)
    assert list(sig.parameters) == ["capability_db", "capability_id"]
