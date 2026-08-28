"""Fixed lists of allowed values.

Why not plain strings? Because ``"capured"`` is a typo that a string would accept
silently and an enum refuses at import time. Every state in this system that can
be compared, stored, or branched on lives here.
"""

from enum import StrEnum

__all__ = [
    "PaymentStatus", "OrderStatus", "IntentStatus", "SubstitutionPolicy",
    "Action", "GateName", "ClarificationReason", "Classification",
]


class PaymentStatus(StrEnum):
    """Where a payment is, according to Razorpay."""
    CREATED = "created"          # order made, nobody has paid yet
    AUTHORIZED = "authorized"    # bank approved, money NOT taken yet
    CAPTURED = "captured"        # money actually taken. Only this counts as paid.
    REFUNDED = "refunded"
    FAILED = "failed"


class OrderStatus(StrEnum):
    """Where an order is, according to the shop."""
    PENDING = "pending"          # waiting for payment — the state we repair
    PROCESSING = "processing"    # paid, being fulfilled
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class IntentStatus(StrEnum):
    """Where a shopping request is in its life."""
    DRAFT = "draft"
    NEEDS_CLARIFICATION = "needs_clarification"   # we must ask the user something
    READY_FOR_SEARCH = "ready_for_search"
    READY_FOR_CHECKOUT = "ready_for_checkout"
    CONFIRMED = "confirmed"                       # user approved the final cart
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class SubstitutionPolicy(StrEnum):
    """What the agent may do when the exact product is unavailable."""
    NEVER = "never"
    ASK_FIRST = "ask_first"                         # default. Safest.
    SAME_BRAND = "same_brand"
    WITHIN_PRICE_DIFFERENCE = "within_price_difference"
    EQUIVALENT = "equivalent"


class Action(StrEnum):
    """What the system decided to do."""
    REPAIR = "repair"        # fix the order — only when every gate passes
    NOOP = "noop"            # nothing to do, correctly
    ESCALATE = "escalate"    # a human must look at this
    BLOCK = "block"          # refuse before any money moves


class Classification(StrEnum):
    """Why a payment and an order disagree."""
    MATCHED_OK = "matched_ok"
    ORDER_PENDING = "order_pending"                     # the main repairable case
    ORDER_ALREADY_PAID = "order_already_paid"
    ORDER_CANCELLED = "order_cancelled"
    ORPHAN_NO_ORDER = "orphan_no_order"
    AMBIGUOUS_MULTI_CANDIDATE = "ambiguous_multi_candidate"
    AMOUNT_MISMATCH = "amount_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    CUSTOMER_MISMATCH = "customer_mismatch"
    AUTHORIZED_NOT_CAPTURED = "authorized_not_captured"
    EXPIRED_TIME_SENSITIVE = "expired_time_sensitive"
    REFUND_ALREADY_ISSUED = "refund_already_issued"


class ClarificationReason(StrEnum):
    """Why the system stopped to ask the user a question.

    Code decides *whether* to ask. The AI only writes the wording.
    """
    MISSING_QUANTITY = "missing_quantity"
    MISSING_BUDGET = "missing_budget"
    MISSING_MERCHANT = "missing_merchant"
    UNCLEAR_PRODUCT = "unclear_product"
    MULTIPLE_EQUAL_MATCHES = "multiple_equal_matches"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    SUBSTITUTION_NEEDED = "substitution_needed"
    OVER_BUDGET = "over_budget"
    CONFLICTS_WITH_PREFERENCE = "conflicts_with_preference"
    AMBIGUOUS_USUAL = "ambiguous_usual"
    CART_DIFFERS_FROM_INTENT = "cart_differs_from_intent"
    LOW_CONFIDENCE = "low_confidence"


class GateName(StrEnum):
    """The 22 safety checks. Frozen at CP-0 — see docs/GATES.md.

    All must pass before money moves. Every one is plain code over typed values,
    so no text and no AI answer can move them.

    PRICES_MATCH was added after CP-0, when building the real Shopify adapter
    showed the original eleven could not catch a merchant quoting one price and
    charging another beneath the cap. Recorded as D-024.

    AUTHORIZATION_FRESH was added after that, answering a specific question:
    what stops a confirmed cart from being paid an hour, a day, or a week
    later, on prices and stock that may no longer be true? A confirmation is
    proof the user approved THIS cart at THIS moment — not a standing
    permission. Recorded as D-035.

    The list is frozen so that a count is never invented, not so that a hole
    stays open.
    """

    # --- before payment: the thirteen mandate gates ---
    MERCHANT_PERMITTED = "G_MERCHANT_PERMITTED"
    INTENT_VALID = "G_INTENT_VALID"
    FIELDS_COMPLETE = "G_FIELDS_COMPLETE"
    CART_UNIQUE = "G_CART_UNIQUE"
    ATTRIBUTES_MATCH = "G_ATTRIBUTES_MATCH"
    QUANTITIES_MATCH = "G_QUANTITIES_MATCH"      # bananas x60 fails here
    PRICES_MATCH = "G_PRICES_MATCH"              # quoted ₹12, charged ₹80: fails here
    ITEMS_AVAILABLE = "G_ITEMS_AVAILABLE"
    CURRENCY_MATCH = "G_CURRENCY_MATCH"
    WITHIN_CAP = "G_WITHIN_CAP"                  # ₹640 under a ₹500 cap fails here
    CONFIRMATION_MATCHES = "G_CONFIRMATION_MATCHES"
    AUTHORIZATION_FRESH = "G_AUTHORIZATION_FRESH"  # confirmed 40 minutes ago: fails here
    IDEMPOTENCY_FREE = "G_IDEMPOTENCY_FREE"

    # --- after payment: the nine integrity gates ---
    PAYMENT_CAPTURED = "G_PAYMENT_CAPTURED"
    NO_REFUND = "G_NO_REFUND"
    AMOUNT_MATCH = "G_AMOUNT_MATCH"              # exact. no tolerance, ever.
    CURRENCY_MATCH_POST = "G_CURRENCY_MATCH_POST"
    SINGLE_CANDIDATE = "G_SINGLE_CANDIDATE"      # two possible orders -> refuse
    CORRELATION = "G_CORRELATION"
    ORDER_REPAIRABLE = "G_ORDER_REPAIRABLE"
    NOT_EXPIRED = "G_NOT_EXPIRED"
    NO_PRIOR_EFFECT = "G_NO_PRIOR_EFFECT"


PRE_PAYMENT_GATES: tuple[GateName, ...] = (
    GateName.MERCHANT_PERMITTED, GateName.INTENT_VALID, GateName.FIELDS_COMPLETE,
    GateName.CART_UNIQUE, GateName.ATTRIBUTES_MATCH, GateName.QUANTITIES_MATCH,
    GateName.PRICES_MATCH,
    GateName.ITEMS_AVAILABLE, GateName.CURRENCY_MATCH, GateName.WITHIN_CAP,
    GateName.CONFIRMATION_MATCHES, GateName.AUTHORIZATION_FRESH,
    GateName.IDEMPOTENCY_FREE,
)

POST_PAYMENT_GATES: tuple[GateName, ...] = (
    GateName.PAYMENT_CAPTURED, GateName.NO_REFUND, GateName.AMOUNT_MATCH,
    GateName.CURRENCY_MATCH_POST, GateName.SINGLE_CANDIDATE, GateName.CORRELATION,
    GateName.ORDER_REPAIRABLE, GateName.NOT_EXPIRED, GateName.NO_PRIOR_EFFECT,
)
