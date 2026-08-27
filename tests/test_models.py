"""Tests for the data shapes.

Most of these check that bad data is *refused*. That matters more than checking
good data works, because bad data reaching a payment is the thing we are
trying to prevent.
"""

import pytest
from pydantic import ValidationError

from orderguard.enums import (
    Action, ClarificationReason, Classification, GateName,
    IntentStatus, OrderStatus, PaymentStatus, SubstitutionPolicy,
    POST_PAYMENT_GATES, PRE_PAYMENT_GATES,
)
from orderguard.models import (
    CartLine, Clarification, Decision, GateResult, IntentItem,
    ObservedCart, Payment, Product, PurchaseIntent, StoreOrder,
)


# --- the fixed lists --------------------------------------------------------

def test_all_twentyone_gates_exist():
    """docs/GATES.md promises 21 gates. Prove the code has 21.

    Twelve before payment since D-024 added PRICES_MATCH; nine after.
    """
    assert len(PRE_PAYMENT_GATES) == 12
    assert len(POST_PAYMENT_GATES) == 9
    assert len(set(PRE_PAYMENT_GATES) | set(POST_PAYMENT_GATES)) == 21


def test_gate_lists_do_not_overlap():
    assert not set(PRE_PAYMENT_GATES) & set(POST_PAYMENT_GATES)


def test_every_gate_name_is_in_a_list():
    """No gate can be defined and then forgotten about."""
    assert set(GateName) == set(PRE_PAYMENT_GATES) | set(POST_PAYMENT_GATES)


def test_typo_in_a_status_is_an_error():
    with pytest.raises(ValueError):
        PaymentStatus("capured")            # missing 't'


# --- the shopping request ---------------------------------------------------

def test_a_normal_request_is_accepted():
    intent = PurchaseIntent(
        intent_id="intent_1", user_id="user_1", merchant="freshcart",
        items=[IntentItem(requested_product="milk", quantity=2, unit="litre")],
        maximum_total_paise=50000,
    )
    assert intent.is_complete
    assert intent.items[0].allow_substitution is SubstitutionPolicy.ASK_FIRST
    assert intent.status is IntentStatus.DRAFT


def test_zero_quantity_is_refused():
    """Buying zero of something is not a purchase."""
    with pytest.raises(ValidationError):
        IntentItem(requested_product="milk", quantity=0, unit="litre")


def test_negative_quantity_is_refused():
    with pytest.raises(ValidationError):
        IntentItem(requested_product="milk", quantity=-3, unit="litre")


def test_negative_budget_is_refused():
    with pytest.raises(ValidationError):
        PurchaseIntent(
            intent_id="i", user_id="u", merchant="m",
            maximum_total_paise=-1,
        )


def test_unexpected_field_is_refused():
    """If the AI invents a field, we find out immediately."""
    with pytest.raises(ValidationError):
        IntentItem(
            requested_product="milk", quantity=1, unit="litre",
            surprise_discount=True,       # not a real field
        )


def test_request_with_missing_info_is_not_complete():
    intent = PurchaseIntent(
        intent_id="i", user_id="u", merchant="m",
        items=[IntentItem(requested_product="milk", quantity=1, unit="litre")],
        maximum_total_paise=0,
        missing_fields=["maximum_total_paise"],
        status=IntentStatus.NEEDS_CLARIFICATION,
    )
    assert not intent.is_complete


# --- the cart ---------------------------------------------------------------

def test_cart_total_adds_up():
    cart = ObservedCart(
        merchant="freshcart",
        lines=[
            CartLine(sku="milk_1l", quantity=2, unit_price_paise=6600),
            CartLine(sku="banana", quantity=6, unit_price_paise=1200),
        ],
        delivery_paise=3000,
    )
    # 13200 + 7200 + 3000
    assert cart.total_paise == 23400


def test_empty_cart_totals_zero():
    assert ObservedCart(merchant="m").total_paise == 0


# --- payments ---------------------------------------------------------------

def test_only_captured_counts_as_paid():
    """AUTHORIZED means the bank approved but no money moved. It auto-refunds."""
    authorized = Payment(payment_id="p1", amount_paise=100, status=PaymentStatus.AUTHORIZED)
    captured = Payment(payment_id="p2", amount_paise=100, status=PaymentStatus.CAPTURED)

    assert not authorized.is_really_paid
    assert captured.is_really_paid


def test_a_refunded_payment_is_not_paid():
    p = Payment(
        payment_id="p3", amount_paise=100,
        status=PaymentStatus.CAPTURED, refunded_paise=100,
    )
    assert not p.is_really_paid


def test_only_pending_orders_can_be_repaired():
    for status in OrderStatus:
        order = StoreOrder(order_id="o", status=status, total_paise=100)
        assert order.is_repairable == (status is OrderStatus.PENDING)


# --- gate results -----------------------------------------------------------

def test_all_gates_passing_allows_the_action():
    result = GateResult.from_checks({
        GateName.QUANTITIES_MATCH: (True, ""),
        GateName.WITHIN_CAP: (True, ""),
    })
    assert result.allow
    assert not result.failed


def test_one_failed_gate_blocks_everything():
    """This is the rule the whole system rests on."""
    result = GateResult.from_checks({
        GateName.QUANTITIES_MATCH: (False, "Requested 6 bananas; cart contains 60"),
        GateName.WITHIN_CAP: (True, ""),
    })
    assert not result.allow
    assert GateName.QUANTITIES_MATCH in result.failed
    assert "60" in result.reasons["G_QUANTITIES_MATCH"]


def test_failure_reasons_are_readable():
    """A refusal a person cannot understand is not much use."""
    result = GateResult.from_checks({
        GateName.WITHIN_CAP: (False, "Cart is ₹640; your limit is ₹500"),
    })
    assert "₹640" in result.reasons["G_WITHIN_CAP"]


def test_gate_result_cannot_be_forced_open():
    """There is no override field. Adding one is an error."""
    with pytest.raises(ValidationError):
        GateResult(allow=True, override=True)


# --- clarifications ---------------------------------------------------------

def test_a_clarification_records_why_code_asked():
    c = Clarification(
        reason=ClarificationReason.MISSING_QUANTITY,
        field="items[0].quantity",
        question="How much milk would you like?",
        options=["500 ml", "1 litre", "2 litres"],
    )
    assert c.reason is ClarificationReason.MISSING_QUANTITY
    assert len(c.options) == 3


def test_a_decision_records_the_gate_result():
    d = Decision(
        payment_id="pay_1",
        classification=Classification.ORDER_PENDING,
        action=Action.ESCALATE,
        gate_result=GateResult(allow=False, failed=[GateName.SINGLE_CANDIDATE]),
        rationale="Two orders match equally.",
    )
    assert d.action is Action.ESCALATE
    assert not d.gate_result.allow
    assert not d.llm_used
