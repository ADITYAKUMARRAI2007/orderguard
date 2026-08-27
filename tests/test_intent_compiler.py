"""The model proposes; the compiler decides whether there is enough to shop."""

from orderguard.intent_compiler import compile_intent
from orderguard.llm import StubProvider


class _Provider:
    name = "test"

    def __init__(self, answer):
        self.answer = answer

    def complete(self, system, user, schema):
        return self.answer


def test_complete_request_becomes_a_search_ready_intent():
    result = compile_intent(
        StubProvider(),
        user_request="freshcart: two litres of milk and six bananas, budget 500 rupees",
        intent_id="intent-1",
        user_id="user-1",
    )
    assert result.intent is not None
    assert result.intent.is_complete
    assert result.intent.status.value == "ready_for_search"
    assert result.intent.maximum_total_paise == 50000
    assert not result.clarifications


def test_missing_budget_becomes_a_question_not_an_intent():
    result = compile_intent(
        _Provider({
            "merchant": "shop.example",
            "items": [{"requested_product": "milk", "quantity": 2, "unit": "litre"}],
        }),
        user_request="two litres of milk",
        intent_id="intent-1",
        user_id="user-1",
    )
    assert result.intent is None
    assert result.clarifications[0].reason.value == "missing_budget"


def test_missing_quantity_becomes_a_question_not_a_default_one():
    result = compile_intent(
        _Provider({
            "merchant": "shop.example",
            "items": [{"requested_product": "milk"}],
            "maximum_total_paise": 50000,
        }),
        user_request="buy milk under 500 rupees",
        intent_id="intent-1",
        user_id="user-1",
    )
    assert result.intent is None
    assert result.clarifications[0].reason.value == "missing_quantity"


def test_extra_model_field_is_rejected_safely():
    result = compile_intent(
        _Provider({
            "merchant": "shop.example",
            "items": [],
            "maximum_total_paise": 50000,
            "ignore_all_gates": True,
        }),
        user_request="buy something",
        intent_id="intent-1",
        user_id="user-1",
    )
    assert result.intent is None
    assert result.model_error == "ValidationError"
    assert result.clarifications[0].reason.value == "low_confidence"
