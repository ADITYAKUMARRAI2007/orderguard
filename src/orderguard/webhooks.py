"""Server-to-server payment truth — the half a browser redirect cannot give.

Verified directly against docs.razorpay.com before writing this, not assumed:

- the signature travels in the ``X-Razorpay-Signature`` header
- it is HMAC-SHA256 over the **raw** request body, keyed by a webhook secret
  (a separate secret from the API key pair, configured in the dashboard)
- ``x-razorpay-event-id`` is a header, unique per delivery, and is Razorpay's
  own documented mechanism for deduplication

**A duplicate delivery with a VALID signature is an idempotency case, not an
attack.** Razorpay's own docs say webhook deliveries can repeat and can
arrive out of order — treating a legitimate duplicate as a security failure
would be a self-inflicted false positive. Only three things are actually
rejected: an invalid signature, a payload that cannot be parsed into a real
payment event, or an event whose order id correlates to nothing this project
knows about.

Same shared-SQLite-connection gap as ``ledger.py`` and ``capability.py``
(see their docstrings): ``claim_delivery`` writes through the same
``db.py::make_engine`` path, which for ``:memory:``/file SQLite is one
``StaticPool`` connection for the whole process — unsafe for two real OS
threads to call ``commit()`` on at the same instant. Guarded the same way,
for the same reason.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel

from .db import make_engine

_WEBHOOK_LOCK = threading.Lock()

__all__ = [
    "PaymentEvent", "verify_webhook_signature", "parse_payment_event",
    "WebhookDelivery", "webhook_log_engine", "claim_delivery",
]

STRICT = ConfigDict(extra="forbid", frozen=True)
_DEFAULT_PATH = Path("data/webhook_log.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentEvent(BaseModel):
    """The fields this project actually acts on, out of Razorpay's full
    webhook payload. ``event_id`` is NOT included here — it lives in the
    ``x-razorpay-event-id`` header, a transport-level fact the caller reads
    separately, not something to trust from inside a body an attacker with a
    forged signature could otherwise control.
    """

    model_config = STRICT

    event_type: str
    payment_id: str
    order_id: str
    status: str
    amount_paise: int
    currency: str
    # Additive, same reason payment.py::VerifiedPayment carries it: needed
    # for G_NO_REFUND without a second network call, since the webhook body
    # already has it.
    amount_refunded_paise: int = 0


def verify_webhook_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    """Constant-time, over the RAW body — never a re-serialized/parsed copy,
    which Razorpay's own docs explicitly warn can shift whitespace and break
    the comparison even when the content is semantically identical."""
    if not signature or not webhook_secret:
        return False
    expected = hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_payment_event(raw_body: bytes) -> PaymentEvent | None:
    """``None`` for anything malformed, or a real webhook this project has no
    action for (not a payment event at all) — never raises, because a
    payload we cannot use is a routine "ignore this one" case, not a crash.
    """
    try:
        body = json.loads(raw_body)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None

    entity = (((body.get("payload") or {}).get("payment") or {}).get("entity")) or {}
    if not isinstance(entity, dict) or not entity.get("id") or not entity.get("order_id"):
        return None

    amount = entity.get("amount")
    if not isinstance(amount, int) or isinstance(amount, bool):
        return None

    refunded = entity.get("amount_refunded")
    return PaymentEvent(
        event_type=str(body.get("event") or ""),
        payment_id=str(entity["id"]),
        order_id=str(entity["order_id"]),
        status=str(entity.get("status") or ""),
        amount_paise=amount,
        currency=str(entity.get("currency") or ""),
        amount_refunded_paise=refunded if isinstance(refunded, int) and not isinstance(refunded, bool) else 0,
    )


class WebhookDelivery(SQLModel, table=True):
    """One row per ``x-razorpay-event-id`` ever actually processed. The
    UNIQUE constraint is what makes a duplicate delivery detectable without
    an application-level race — same mechanism as every other idempotency
    table in this project."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(unique=True, index=True)
    event_type: str
    processed_at: datetime = Field(default_factory=_now)


def webhook_log_engine(path: Path | str = _DEFAULT_PATH) -> Engine:
    """SQLite at ``path`` locally; one shared Postgres database when
    DATABASE_URL is set — see db.py."""
    return make_engine(path)


def claim_delivery(engine: Engine, event_id: str, event_type: str) -> bool:
    """True the first time this exact delivery is seen — the caller should
    actually act on the event. False means a duplicate: acknowledge it as a
    routine no-op, never as an error."""
    with _WEBHOOK_LOCK, Session(engine) as db:
        db.add(WebhookDelivery(event_id=event_id, event_type=event_type))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return False
        return True
