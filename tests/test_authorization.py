"""Signed once, verified independently, consumed exactly once.

Three properties, three groups of tests: the signature actually protects the
payload (tampering is detected), the payload itself never changes after
issuance (frozen), and consumption is single-use under the same
UNIQUE-constraint guarantee the payment ledger already relies on.
"""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from orderguard.authorization import (
    authorization_db_engine, consume_authorization, get_consumption,
    is_expired, issue_authorization, verify_authorization,
)


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def engine():
    return authorization_db_engine(":memory:")


def _issue(signing_key, **overrides):
    data = dict(
        transaction_id="txn1", intent_hash="a" * 64, cart_hash="b" * 64,
        merchant="freshcart", amount_paise=16200, currency="INR",
        provenance="direct_verified", audit_tip="c" * 64, signing_key=signing_key,
    )
    data.update(overrides)
    return issue_authorization(**data)


# --- signature actually protects the payload --------------------------------

def test_a_freshly_issued_authorization_verifies(signing_key):
    auth = _issue(signing_key)
    assert verify_authorization(auth, public_key=signing_key.public_key())


def test_tampering_the_amount_after_issuance_is_detected(signing_key):
    auth = _issue(signing_key)
    tampered = auth.model_copy(update={"amount_paise": 999999})
    assert not verify_authorization(tampered, public_key=signing_key.public_key())


def test_tampering_the_merchant_after_issuance_is_detected(signing_key):
    auth = _issue(signing_key)
    tampered = auth.model_copy(update={"merchant": "a-different-shop.example"})
    assert not verify_authorization(tampered, public_key=signing_key.public_key())


def test_tampering_expires_at_after_issuance_is_detected(signing_key):
    """The exact attack a mutable-consumed-flag design would have permitted:
    quietly extending validity by editing a field after the fact."""
    auth = _issue(signing_key)
    tampered = auth.model_copy(update={"expires_at": auth.expires_at + timedelta(days=365)})
    assert not verify_authorization(tampered, public_key=signing_key.public_key())


def test_a_signature_from_a_different_key_does_not_verify(signing_key):
    auth = _issue(signing_key)
    wrong_key = Ed25519PrivateKey.generate()
    assert not verify_authorization(auth, public_key=wrong_key.public_key())


def test_a_garbled_signature_fails_closed_not_with_an_exception(signing_key):
    auth = _issue(signing_key)
    garbled = auth.model_copy(update={"signature": "not-hex-at-all"})
    assert not verify_authorization(garbled, public_key=signing_key.public_key())


# --- the payload is genuinely frozen ----------------------------------------

def test_the_authorization_cannot_be_mutated_in_place(signing_key):
    auth = _issue(signing_key)
    with pytest.raises(ValidationError):
        auth.amount_paise = 1


def test_extra_fields_are_rejected():
    from orderguard.authorization import Authorization

    with pytest.raises(ValidationError):
        Authorization.model_validate({
            "authorization_id": "og_auth_x", "transaction_id": "t", "intent_hash": "a",
            "cart_hash": "b", "merchant": "freshcart", "amount_paise": 100,
            "currency": "INR", "provenance": "direct", "issued_at": "2026-08-29T00:00:00Z",
            "expires_at": "2026-08-29T00:15:00Z", "audit_tip": None, "signature": "",
            "discount_applied": True,      # not part of the frozen contract
        })


# --- expiry, same TTL as G_AUTHORIZATION_FRESH ------------------------------

def test_not_yet_expired_just_after_issuance(signing_key):
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    auth = _issue(signing_key, now=now)
    assert not is_expired(auth, now=now + timedelta(minutes=1))


def test_expired_after_the_ttl_elapses(signing_key):
    now = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    auth = _issue(signing_key, now=now)
    assert is_expired(auth, now=now + timedelta(minutes=16))


# --- single-use consumption, same guarantee as the ledger -------------------

def test_the_first_consumption_wins(engine, signing_key):
    auth = _issue(signing_key)
    entry, first_time = consume_authorization(
        engine, authorization_id=auth.authorization_id, razorpay_order_id="order_1",
    )
    assert first_time is True
    assert entry.razorpay_order_id == "order_1"


def test_a_second_consumption_attempt_gets_back_the_original_not_a_new_one(engine, signing_key):
    auth = _issue(signing_key)
    consume_authorization(engine, authorization_id=auth.authorization_id, razorpay_order_id="order_1")

    entry, first_time = consume_authorization(
        engine, authorization_id=auth.authorization_id, razorpay_order_id="order_2",
    )
    assert first_time is False
    assert entry.razorpay_order_id == "order_1"          # not order_2 — the replay is ignored


def test_seventy_duplicate_consumption_attempts_produce_one_business_effect(engine, signing_key):
    """Same property test_payment_flow.py already proves for the ledger,
    applied here to authorization consumption specifically."""
    auth = _issue(signing_key)
    results = [
        consume_authorization(engine, authorization_id=auth.authorization_id, razorpay_order_id=f"order_{i}")
        for i in range(70)
    ]
    assert sum(1 for _, first_time in results if first_time) == 1


def test_an_unconsumed_authorization_has_no_consumption_record(engine, signing_key):
    auth = _issue(signing_key)
    assert get_consumption(engine, auth.authorization_id) is None
