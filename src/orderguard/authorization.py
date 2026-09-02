"""Ed25519-signed authorization receipts — AP2-inspired, not AP2-compliant.

Wraps what already exists rather than reinventing it: ``confirmed_cart_hash``
(D-004), the freshness window `checkout_guard.DEFAULT_AUTHORIZATION_TTL`
(D-035), and the ledger's UNIQUE-constraint single-use pattern (`ledger.py`).
What is new here is a single, portable, cryptographically signed artifact
that names all of it together — a receipt a judge, or a future session, can
independently verify without re-running the gates.

**AP2-inspired, never "AP2 compliant".** Google's AP2 v0.2 spec favours a
non-deterministic signature scheme (ECDSA-style, via SD-JWT) for its Checkout
Mandate binding. This uses Ed25519 — simpler, deterministic, and entirely our
own artifact. Naming the resemblance is honest; claiming conformance to a
specification this project does not implement would not be.

**Immutable once signed, on purpose.** An earlier draft put a mutable
``consumed`` flag inside the signed payload. Any field inside a signed
payload that changes after issuance invalidates the signature it is supposed
to protect. So the signed ``Authorization`` never changes after
``issue_authorization`` returns it — `frozen=True` enforces this at the type
level, not just by convention — and single-use consumption is tracked in a
completely separate table, ``AuthorizationConsumption``, written once via the
same UNIQUE-constraint pattern ``ledger.claim_order`` already uses.

**"Tamper-evident", not "tamper-proof".** A signature proves the payload has
not changed since a holder of the private key produced it. It says nothing
about whether the private key itself has been compromised — that is a key-
management problem, not a cryptography problem, and out of scope for what
this artifact claims to prove.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .audit import canonical_json
from .checkout_guard import DEFAULT_AUTHORIZATION_TTL

__all__ = [
    "Authorization", "AuthorizationConsumption",
    "load_or_create_signing_key", "issue_authorization", "verify_authorization",
    "is_expired", "authorization_db_engine", "consume_authorization", "get_consumption",
]

STRICT = ConfigDict(extra="forbid", frozen=True)
_DEFAULT_KEY_PATH = Path("data/authorization_signing_key.pem")
_DEFAULT_DB_PATH = Path("data/authorization.db")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Authorization(BaseModel):
    """Signed once. Every field below is covered by ``signature`` except
    ``signature`` itself. Nothing here is ever mutated after issuance —
    ``frozen=True`` makes an accidental in-place edit a runtime error rather
    than a silently invalidated signature."""

    model_config = STRICT

    authorization_id: str
    transaction_id: str
    intent_hash: str
    cart_hash: str
    merchant: str
    amount_paise: int
    currency: str
    provenance: str              # e.g. connectors.Evidence value, or "direct"
    issued_at: datetime
    expires_at: datetime
    audit_tip: str | None        # AuditEvent.entry_hash at time of issuance, if any
    signature: str = ""


class AuthorizationConsumption(SQLModel, table=True):
    """Written once, separately from the signed payload it refers to. The
    UNIQUE constraint on ``authorization_id`` is what makes consumption
    single-use — same mechanism as ledger.LedgerEntry.idempotency_key."""

    id: int | None = Field(default=None, primary_key=True)
    authorization_id: str = Field(unique=True, index=True)
    razorpay_order_id: str
    audit_event_id: int | None = None
    consumed_at: datetime = Field(default_factory=_now)


def load_or_create_signing_key(path: Path | str = _DEFAULT_KEY_PATH) -> Ed25519PrivateKey:
    """One key per deployment, generated on first use, never regenerated
    silently afterward — a receipt signed yesterday must still verify today."""
    path = Path(path)
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return key


def _signable_bytes(auth: Authorization) -> bytes:
    payload = auth.model_dump(mode="json", exclude={"signature"})
    return canonical_json(payload).encode()


def issue_authorization(
    *, transaction_id: str, intent_hash: str, cart_hash: str, merchant: str,
    amount_paise: int, currency: str, provenance: str, audit_tip: str | None,
    signing_key: Ed25519PrivateKey | None = None,
    now: datetime | None = None, ttl: timedelta = DEFAULT_AUTHORIZATION_TTL,
) -> Authorization:
    """Freeze and sign. The same TTL G_AUTHORIZATION_FRESH already enforces
    (D-035), so a confirmed-but-stale cart and an expired receipt fail at the
    same horizon rather than one silently outliving the other."""
    now = now or _now()
    unsigned = Authorization(
        authorization_id=f"og_auth_{uuid4().hex[:16]}",
        transaction_id=transaction_id, intent_hash=intent_hash, cart_hash=cart_hash,
        merchant=merchant, amount_paise=amount_paise, currency=currency,
        provenance=provenance, issued_at=now, expires_at=now + ttl,
        audit_tip=audit_tip, signature="",
    )
    key = signing_key or load_or_create_signing_key()
    signature = key.sign(_signable_bytes(unsigned))
    return unsigned.model_copy(update={"signature": signature.hex()})


def verify_authorization(
    auth: Authorization, *, public_key: Ed25519PublicKey | None = None,
) -> bool:
    """Recomputes trust from the payload and signature alone — never trusts
    a stored 'this is valid' flag, because there isn't one."""
    key = public_key or load_or_create_signing_key().public_key()
    try:
        signature = bytes.fromhex(auth.signature)
    except ValueError:
        return False
    try:
        key.verify(signature, _signable_bytes(auth))
        return True
    except InvalidSignature:
        return False


def is_expired(auth: Authorization, now: datetime | None = None) -> bool:
    return (now or _now()) >= auth.expires_at


def authorization_db_engine(path: Path | str = _DEFAULT_DB_PATH) -> Engine:
    """Same shape as ledger.ledger_engine / audit.audit_engine."""
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


def consume_authorization(
    engine: Engine, *, authorization_id: str, razorpay_order_id: str,
    audit_event_id: int | None = None,
) -> tuple[AuthorizationConsumption, bool]:
    """Claim single-use consumption, or hand back the original claim.

    Returns ``(entry, first_time)`` — the exact shape of
    ``ledger.claim_order``. Only the caller that wins the INSERT actually
    consumed this authorization; every later call, however many times it is
    retried, gets back the ORIGINAL consumption record untouched.
    """
    with Session(engine) as db:
        entry = AuthorizationConsumption(
            authorization_id=authorization_id, razorpay_order_id=razorpay_order_id,
            audit_event_id=audit_event_id,
        )
        db.add(entry)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.exec(
                select(AuthorizationConsumption)
                .where(AuthorizationConsumption.authorization_id == authorization_id)
            ).one()
            return existing, False
        db.refresh(entry)
        return entry, True


def get_consumption(engine: Engine, authorization_id: str) -> AuthorizationConsumption | None:
    with Session(engine) as db:
        return db.exec(
            select(AuthorizationConsumption)
            .where(AuthorizationConsumption.authorization_id == authorization_id)
        ).first()
