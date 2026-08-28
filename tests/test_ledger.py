"""The idempotency ledger: at-least-once calls, exactly-once business effect.

The number that matters in this file is 70 — the buildathon's own framing of
the property ("70 duplicate events -> exactly 1 business effect"). Every test
either proves a claim happens once, or proves a finalize happens once.
"""

import pytest

from orderguard.ledger import (
    LedgerStatus,
    claim_order,
    finalize_if_pending,
    get_entry,
    ledger_engine,
    reject,
)


@pytest.fixture
def engine():
    return ledger_engine(":memory:")


def _claim(engine, key="m|intent-1|purchase|hash-1", amount=29900):
    return claim_order(
        engine, idempotency_key=key, merchant="freshcart",
        purchase_intent_id="intent-1", cart_hash="hash-1",
        expected_amount_paise=amount, currency="INR",
    )


# --- claiming an order --------------------------------------------------------

def test_the_first_claim_creates_a_new_row(engine):
    entry, created = _claim(engine)
    assert created is True
    assert entry.status is LedgerStatus.PENDING
    assert entry.expected_amount_paise == 29900


def test_a_second_claim_with_the_same_key_gets_the_existing_row(engine):
    first, _ = _claim(engine)
    second, created = _claim(engine)
    assert created is False
    assert second.id == first.id


def test_seventy_claims_for_the_same_key_create_exactly_one_row(engine):
    """The scenario the buildathon names explicitly."""
    results = [_claim(engine) for _ in range(70)]
    created_count = sum(1 for _, created in results if created)
    assert created_count == 1

    ids = {entry.id for entry, _ in results}
    assert len(ids) == 1                     # every caller got the SAME row


def test_a_different_cart_hash_is_a_different_purchase(engine):
    """An edited cart is a new purchase attempt, not a retry of the old one
    (D-004): re-confirming produces a new cart_hash, hence a new key."""
    _, created_a = _claim(engine, key="m|intent-1|purchase|hash-1")
    _, created_b = _claim(engine, key="m|intent-1|purchase|hash-2")
    assert created_a and created_b


def test_attaching_an_order_id_is_visible_to_every_later_claim(engine):
    from orderguard.ledger import attach_order

    entry, _ = _claim(engine)
    attach_order(engine, entry.idempotency_key, "order_real_razorpay_id")

    again, created = _claim(engine)
    assert created is False
    assert again.razorpay_order_id == "order_real_razorpay_id"


# --- finalizing a payment ------------------------------------------------------

def test_the_first_finalize_captures_the_row(engine):
    entry, _ = _claim(engine)
    updated, won = finalize_if_pending(
        engine, idempotency_key=entry.idempotency_key,
        razorpay_payment_id="pay_abc", captured_amount_paise=29900,
    )
    assert won is True
    assert updated.status is LedgerStatus.CAPTURED
    assert updated.razorpay_payment_id == "pay_abc"
    assert updated.resolved_at is not None


def test_seventy_finalize_calls_capture_exactly_once(engine):
    """The property the entire ledger exists for.

    70 identical requests — a retried client, a duplicate webhook, a replay
    attack — must produce ONE captured state, and every caller after the first
    must see that same original result rather than writing anything of their
    own.
    """
    entry, _ = _claim(engine)

    results = [
        finalize_if_pending(
            engine, idempotency_key=entry.idempotency_key,
            razorpay_payment_id="pay_abc", captured_amount_paise=29900,
        )
        for _ in range(70)
    ]

    won_count = sum(1 for _, won in results if won)
    assert won_count == 1

    final = get_entry(engine, entry.idempotency_key)
    assert final.status is LedgerStatus.CAPTURED
    assert final.razorpay_payment_id == "pay_abc"


def test_a_second_finalize_with_a_different_payment_id_cannot_overwrite_the_first():
    """The attack this guards against: a captured purchase, then someone
    (client bug or adversary) tries to finalize the same key again with a
    DIFFERENT payment_id. The first payment recorded must never change."""
    engine = ledger_engine(":memory:")
    entry, _ = _claim(engine)

    finalize_if_pending(
        engine, idempotency_key=entry.idempotency_key,
        razorpay_payment_id="pay_first", captured_amount_paise=29900,
    )
    second, won = finalize_if_pending(
        engine, idempotency_key=entry.idempotency_key,
        razorpay_payment_id="pay_ATTACKER_REPLAY", captured_amount_paise=29900,
    )

    assert won is False
    assert second.razorpay_payment_id == "pay_first"      # unchanged


def test_finalize_before_any_claim_captures_nothing(engine):
    """There is no row to update. Must not create one out of thin air."""
    updated, won = finalize_if_pending(
        engine, idempotency_key="never-claimed",
        razorpay_payment_id="pay_x", captured_amount_paise=100,
    )
    assert won is False
    assert updated is None or updated.status is not LedgerStatus.CAPTURED


# --- rejection is not terminal -------------------------------------------------

def test_a_rejected_attempt_can_still_be_finalized_later(engine):
    """Rejecting one bad claim (wrong signature, wrong amount) must not burn
    the user's only chance to complete the SAME purchase correctly afterwards."""
    entry, _ = _claim(engine)
    reject(engine, entry.idempotency_key, "signature did not match")

    still_pending = get_entry(engine, entry.idempotency_key)
    assert still_pending.status is LedgerStatus.PENDING
    assert still_pending.last_rejection_reason == "signature did not match"

    updated, won = finalize_if_pending(
        engine, idempotency_key=entry.idempotency_key,
        razorpay_payment_id="pay_correct_this_time", captured_amount_paise=29900,
    )
    assert won is True
    assert updated.status is LedgerStatus.CAPTURED


def test_rejecting_an_already_captured_row_does_not_reopen_it(engine):
    entry, _ = _claim(engine)
    finalize_if_pending(
        engine, idempotency_key=entry.idempotency_key,
        razorpay_payment_id="pay_abc", captured_amount_paise=29900,
    )
    reject(engine, entry.idempotency_key, "a stray rejection call")

    final = get_entry(engine, entry.idempotency_key)
    assert final.status is LedgerStatus.CAPTURED     # never moved backwards
