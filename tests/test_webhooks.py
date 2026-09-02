"""Signature first, dedup second, correlation third — the order matters.

A bad signature must never even reach parsing. A duplicate delivery must
never be treated as an error. Only real malformation or a genuinely unknown
transaction gets refused.
"""

import hashlib
import hmac
import json

import pytest

from orderguard.webhooks import (
    claim_delivery, parse_payment_event, verify_webhook_signature, webhook_log_engine,
)

SECRET = "whsec_fake_for_tests"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(event="payment.captured", payment_id="pay_123", order_id="order_abc",
             status="captured", amount=29900, currency="INR") -> bytes:
    return json.dumps({
        "entity": "event", "event": event,
        "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "status": status,
            "amount": amount, "currency": currency,
        }}},
    }).encode()


# --- signature verification --------------------------------------------------

def test_a_correctly_signed_body_verifies():
    body = _payload()
    assert verify_webhook_signature(body, _sign(body), SECRET)


def test_a_body_signed_with_the_wrong_secret_fails():
    body = _payload()
    assert not verify_webhook_signature(body, _sign(body, "wrong_secret"), SECRET)


def test_a_single_byte_change_after_signing_is_detected():
    body = _payload()
    signature = _sign(body)
    tampered = body.replace(b"29900", b"29901")
    assert not verify_webhook_signature(tampered, signature, SECRET)


def test_an_empty_signature_or_secret_never_verifies():
    body = _payload()
    assert not verify_webhook_signature(body, "", SECRET)
    assert not verify_webhook_signature(body, _sign(body), "")


# --- parsing: malformed payloads never raise ---------------------------------

def test_a_well_formed_payment_captured_event_parses():
    event = parse_payment_event(_payload())
    assert event.event_type == "payment.captured"
    assert event.payment_id == "pay_123"
    assert event.order_id == "order_abc"
    assert event.amount_paise == 29900


def test_non_json_body_returns_none_not_an_exception():
    assert parse_payment_event(b"not json at all { [ }") is None


def test_json_that_is_not_an_object_returns_none():
    assert parse_payment_event(b"[1, 2, 3]") is None


def test_a_payload_missing_the_payment_entity_returns_none():
    assert parse_payment_event(json.dumps({"event": "order.paid"}).encode()) is None


def test_a_payment_entity_missing_an_order_id_returns_none():
    body = json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": "pay_123", "amount": 100}}},
    }).encode()
    assert parse_payment_event(body) is None


def test_a_non_integer_amount_returns_none_rather_than_a_wrong_number():
    body = json.dumps({
        "event": "payment.captured",
        "payload": {"payment": {"entity": {
            "id": "pay_1", "order_id": "order_1", "amount": "29900",  # string, not int
        }}},
    }).encode()
    assert parse_payment_event(body) is None


# --- deduplication: a repeat delivery is a no-op, never an error -------------

def test_the_first_delivery_of_an_event_id_is_claimed():
    engine = webhook_log_engine(":memory:")
    assert claim_delivery(engine, "evt_1", "payment.captured") is True


def test_a_repeated_delivery_of_the_same_event_id_is_not_claimed_again():
    engine = webhook_log_engine(":memory:")
    claim_delivery(engine, "evt_1", "payment.captured")
    assert claim_delivery(engine, "evt_1", "payment.captured") is False


def test_seventy_duplicate_deliveries_are_claimed_exactly_once():
    engine = webhook_log_engine(":memory:")
    results = [claim_delivery(engine, "evt_1", "payment.captured") for _ in range(70)]
    assert sum(results) == 1


def test_different_event_ids_are_independent_claims():
    engine = webhook_log_engine(":memory:")
    assert claim_delivery(engine, "evt_1", "payment.captured") is True
    assert claim_delivery(engine, "evt_2", "payment.captured") is True
