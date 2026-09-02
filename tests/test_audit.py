"""The audit chain proves itself, or says exactly where it stopped proving itself.

docs/API_CONTRACTS.md #7 has specified AuditEvent's shape since CP-0. It was
never implemented until now (F-030) — these tests are the first evidence that
the shape actually does what the contract claims: append-only, and any
retrospective edit is detectable at the exact point it happened.
"""

import pytest

from orderguard.audit import (
    ChainTampered, append_event, audit_engine, canonical_json, event_payload,
    verify_chain,
)


@pytest.fixture
def engine():
    return audit_engine(":memory:")


def test_the_first_event_has_no_predecessor(engine):
    event = append_event(engine, "intent_recorded", {"merchant": "freshcart"})
    assert event.seq == 0
    assert event.prev_hash is None
    assert event.entry_hash  # a real sha256 hex digest, not empty


def test_each_event_links_to_the_one_before_it(engine):
    first = append_event(engine, "intent_recorded", {"item": "milk"})
    second = append_event(engine, "cart_verified", {"allow": True})
    assert second.seq == first.seq + 1
    assert second.prev_hash == first.entry_hash


def test_the_same_payload_in_different_key_order_hashes_identically(engine):
    a = append_event(engine, "gate_result", {"allow": True, "merchant": "freshcart"})
    assert canonical_json({"merchant": "freshcart", "allow": True}) == canonical_json(
        {"allow": True, "merchant": "freshcart"}
    )
    # proves canonical_json, not the DB, is what makes replay-safe hashing possible
    assert a.entry_hash == append_event(
        audit_engine(":memory:"), "gate_result", {"merchant": "freshcart", "allow": True}
    ).entry_hash


def test_a_refusal_is_recorded_with_the_same_weight_as_an_action(engine):
    """The exception list is the deliverable, not an apology."""
    event = append_event(engine, "gate_blocked", {
        "reason": "Cart total exceeds the approved spending cap.",
        "gate": "G_WITHIN_CAP",
    })
    assert event_payload(event)["gate"] == "G_WITHIN_CAP"


def test_verify_chain_passes_over_untampered_history(engine):
    for i in range(5):
        append_event(engine, "step", {"i": i})
    events = verify_chain(engine)
    assert len(events) == 5
    assert [e.seq for e in events] == [0, 1, 2, 3, 4]


def test_many_sequential_appends_get_distinct_monotonic_seqs(engine):
    """Mirrors the ledger's 70-duplicate-call proof: the same mechanical
    guarantee (a database-enforced uniqueness constraint, not an
    application-level check) applies here to sequence numbers instead of
    idempotency keys."""
    events = [append_event(engine, "step", {"i": i}) for i in range(70)]
    assert [e.seq for e in events] == list(range(70))
    assert len(verify_chain(engine)) == 70


# --- tamper detection: the actual point of this module --------------------

def test_editing_a_payload_after_the_fact_is_detected(engine):
    from sqlmodel import Session, select

    from orderguard.audit import AuditEvent

    append_event(engine, "gate_result", {"allow": True})
    append_event(engine, "payment_captured", {"amount_paise": 50000})

    with Session(engine) as db:
        row = db.exec(select(AuditEvent).where(AuditEvent.seq == 0)).one()
        row.payload_json = '{"allow":false}'  # the tamper: flip an already-recorded decision
        db.add(row)
        db.commit()

    with pytest.raises(ChainTampered) as excinfo:
        verify_chain(engine)
    assert excinfo.value.seq == 0


def test_tampering_an_early_event_is_caught_even_though_later_events_are_untouched(engine):
    """The break is detected at the event that was actually edited, not
    wherever the corruption happens to first become visible."""
    from sqlmodel import Session, select

    from orderguard.audit import AuditEvent

    append_event(engine, "step", {"i": 0})
    append_event(engine, "step", {"i": 1})
    append_event(engine, "step", {"i": 2})

    with Session(engine) as db:
        row = db.exec(select(AuditEvent).where(AuditEvent.seq == 1)).one()
        row.payload_json = '{"i":999}'
        db.add(row)
        db.commit()

    with pytest.raises(ChainTampered) as excinfo:
        verify_chain(engine)
    assert excinfo.value.seq == 1


def test_rewriting_prev_hash_alone_is_also_detected(engine):
    """Even a tamper that leaves every payload untouched — just re-pointing
    the chain — breaks verification, because prev_hash is checked against
    the actual prior event, not merely required to be present."""
    from sqlmodel import Session, select

    from orderguard.audit import AuditEvent

    append_event(engine, "step", {"i": 0})
    append_event(engine, "step", {"i": 1})

    with Session(engine) as db:
        row = db.exec(select(AuditEvent).where(AuditEvent.seq == 1)).one()
        row.prev_hash = "0" * 64
        db.add(row)
        db.commit()

    with pytest.raises(ChainTampered) as excinfo:
        verify_chain(engine)
    assert excinfo.value.seq == 1


def test_an_empty_chain_verifies_trivially(engine):
    assert verify_chain(engine) == []
