"""The data shapes.

Two rules run through all of this:

1. Money is always ``*_paise`` and always an ``int``.
2. Every field is checked. A wrong value raises an error instead of
   travelling quietly into a payment.

The most important shape is ``PurchaseIntent``. It is the written-down version of
what the user asked for, and every later step compares reality against it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    Action, ClarificationReason, Classification, GateName,
    IntentStatus, OrderStatus, PaymentStatus, SubstitutionPolicy,
)

__all__ = [
    "IntentItem", "PurchaseIntent", "Product", "CartLine", "ObservedCart",
    "Payment", "StoreOrder", "GateResult", "Clarification",
]

# extra="forbid" means an unexpected field is an error.
# If the AI invents a field, we find out immediately instead of ignoring it.
STRICT = ConfigDict(extra="forbid", frozen=False)


# --- what the user asked for ------------------------------------------------

class IntentItem(BaseModel):
    """One line of a shopping request."""
    model_config = STRICT

    requested_product: str = Field(min_length=1)
    quantity: int = Field(ge=1)          # ge=1 not gt=0 — see FAILURE_LOG F-003
    unit: str = Field(min_length=1)

    # must match, or the cart is wrong (gate: ATTRIBUTES_MATCH)
    required_attributes: dict[str, str] = Field(default_factory=dict)
    # nice to have. never blocks anything.
    preferred_attributes: dict[str, str] = Field(default_factory=dict)

    allow_substitution: SubstitutionPolicy = SubstitutionPolicy.ASK_FIRST


class PurchaseIntent(BaseModel):
    """The contract. Everything downstream is checked against this.

    ``confirmed_cart_hash`` is set once, when the user approves the final cart,
    and never recomputed. If the cart changes afterwards, this is a different
    purchase and needs fresh approval. (See DECISIONS.md D-004.)
    """
    model_config = STRICT

    intent_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)

    # Empty means the shopper did not name a store, which is the normal case:
    # OrderGuard searches the allowed stores and the store is decided when the
    # user picks an offer. A non-empty value is a *constraint* the user stated
    # ("from Blue Tokai") and the selection must honour it.
    #
    # This is never the permission check. G_MERCHANT_PERMITTED does that against
    # the allowed list, and CartExpectation.merchant — which is what the cart is
    # actually compared to — stays required.
    merchant: str = ""
    items: list[IntentItem] = Field(default_factory=list)

    maximum_total_paise: int = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    delivery_deadline: datetime | None = None
    requires_final_confirmation: bool = True

    missing_fields: list[str] = Field(default_factory=list)
    status: IntentStatus = IntentStatus.DRAFT
    confirmed_cart_hash: str | None = None
    # Set alongside confirmed_cart_hash, never independently. This is what
    # G_AUTHORIZATION_FRESH checks: a confirmation is proof the user approved
    # THIS cart at THIS moment, not a standing permission. Left unbounded, a
    # confirmed hash would authorise a checkout an hour, a day, or a week
    # later — a classic time-of-check/time-of-use gap between "the user looked
    # at this cart" and "money moved". See D-035.
    confirmed_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        """True when nothing still needs asking."""
        return not self.missing_fields and bool(self.items)


# --- what the shop actually has ---------------------------------------------

class Product(BaseModel):
    model_config = STRICT

    sku: str = Field(min_length=1)
    title: str = Field(min_length=1)
    price_paise: int = Field(ge=0)
    in_stock: int = Field(ge=0)
    category: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class CartLine(BaseModel):
    model_config = STRICT

    sku: str = Field(min_length=1)
    # External stores identify a purchasable variant separately from their
    # product. FreshCart uses the SKU for both, so these remain optional.
    line_id: str = ""
    variant_id: str = ""
    title: str = ""
    quantity: int = Field(ge=1)
    # A platform may quote a line total without a safely divisible unit price
    # (for example, a line-level discount on three units). Keep the exact line
    # total authoritative rather than inventing a rounded per-unit price.
    unit_price_paise: int | None = Field(default=None, ge=0)
    line_total_paise: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def has_an_exact_line_total(self) -> "CartLine":
        if self.line_total_paise is None:
            if self.unit_price_paise is None:
                raise ValueError("a cart line needs a unit price or exact line total")
            self.line_total_paise = self.quantity * self.unit_price_paise
        return self


class ObservedCart(BaseModel):
    """What is *actually* in the cart, read back from the shop.

    Not what the agent believes it added. Reading this back and comparing it to
    the intent is the whole point of the project.
    """
    model_config = STRICT

    merchant: str = Field(min_length=1)
    cart_id: str = ""
    lines: list[CartLine] = Field(default_factory=list)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    subtotal_paise: int | None = Field(default=None, ge=0)
    delivery_paise: int = Field(default=0, ge=0)
    total_paise: int | None = Field(default=None, ge=0)
    checkout_url: str = ""

    @model_validator(mode="after")
    def has_exact_totals(self) -> "ObservedCart":
        line_total = sum(line.line_total_paise or 0 for line in self.lines)
        if self.subtotal_paise is None:
            self.subtotal_paise = line_total
        if self.total_paise is None:
            self.total_paise = self.subtotal_paise + self.delivery_paise
        return self


# --- what the payment world says --------------------------------------------

class Payment(BaseModel):
    model_config = STRICT

    payment_id: str = Field(min_length=1)
    razorpay_order_id: str | None = None
    amount_paise: int = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    status: PaymentStatus
    method: str = ""
    email: str | None = None
    contact: str | None = None
    refunded_paise: int = Field(default=0, ge=0)
    created_at: datetime | None = None
    captured_at: datetime | None = None
    notes: dict[str, str] = Field(default_factory=dict)

    @property
    def is_really_paid(self) -> bool:
        """Only CAPTURED counts. AUTHORIZED means the bank approved but the
        money has not moved, and it auto-refunds if left alone."""
        return self.status is PaymentStatus.CAPTURED and self.refunded_paise == 0


class StoreOrder(BaseModel):
    model_config = STRICT

    order_id: str = Field(min_length=1)
    status: OrderStatus
    total_paise: int = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    email: str | None = None
    contact: str | None = None
    razorpay_payment_id: str | None = None
    razorpay_order_id: str | None = None
    purchase_intent_id: str | None = None
    is_time_sensitive: bool = False
    fulfilment_deadline: datetime | None = None
    created_at: datetime | None = None

    @property
    def is_repairable(self) -> bool:
        """Only a pending order can be repaired. We never revive a cancelled one
        automatically — that needs a human decision."""
        return self.status is OrderStatus.PENDING


# --- results ----------------------------------------------------------------

class GateResult(BaseModel):
    """The outcome of running the safety checks.

    ``allow`` is True only when nothing failed. There is no override flag and
    no way for an AI answer to set it.
    """
    model_config = STRICT

    allow: bool
    passed: list[GateName] = Field(default_factory=list)
    failed: list[GateName] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_checks(cls, results: dict[GateName, tuple[bool, str]]) -> "GateResult":
        """Build from ``{gate: (passed?, reason_if_failed)}``."""
        passed = [g for g, (ok, _) in results.items() if ok]
        failed = [g for g, (ok, _) in results.items() if not ok]
        reasons = {str(g): why for g, (ok, why) in results.items() if not ok}
        return cls(allow=not failed, passed=passed, failed=failed, reasons=reasons)


class Clarification(BaseModel):
    """A question we must ask before going further.

    Code decides *whether* to ask (``reason``). The AI only writes ``question``.
    """
    model_config = STRICT

    reason: ClarificationReason
    field: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    model_config = STRICT

    payment_id: str
    order_id: str | None = None
    classification: Classification
    action: Action
    gate_result: GateResult
    llm_used: bool = False
    rationale: str = ""
