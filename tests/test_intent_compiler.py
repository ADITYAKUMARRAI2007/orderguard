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


# --- answering a question actually answers it -------------------------------

def test_a_bare_answer_is_bound_to_the_question_it_answers():
    """F-014: "how many?" answered with "2" used to be appended as a lone "2".

    Next to a request already containing "under 400", that is ambiguous, and the
    same question came back forever. Labelling the answer ends the loop.
    """
    from orderguard.intent_compiler import label_answer

    assert label_answer("items[0].quantity", "2") == "Quantity for item 1: 2"
    assert label_answer("items[1].quantity", "three") == "Quantity for item 2: three"
    assert (
        label_answer("maximum_total_paise", "200")
        == "Total budget including delivery: 200 rupees"
    )


def test_a_number_written_the_way_people_write_it_is_still_read():
    from orderguard.intent_compiler import label_answer

    assert label_answer("maximum_total_paise", "₹1,200") == (
        "Total budget including delivery: 1200 rupees"
    )
    assert label_answer("items[0].quantity", "just 2 please") == "Quantity for item 1: 2"


def test_the_named_shop_survives_an_incomplete_request():
    """The shop must be checkable before every other question is answered.

    Otherwise we ask a budget for a shop we were never going to be able to use.
    """
    from orderguard.intent_compiler import compile_intent

    class _Draft:
        name = "test"

        def complete(self, system, user, schema):
            return {"merchant": "La Pinoz",
                    "items": [{"requested_product": "pizza", "quantity": 2}]}

    result = compile_intent(_Draft(), user_request="x", intent_id="i", user_id="u")

    assert result.intent is None                 # still missing a budget
    assert result.draft_merchant == "La Pinoz"   # and yet we know the shop


def test_a_provider_outage_does_not_blame_the_user():
    """F-017: an outage was reported as "I could not understand that order",
    which invites the user to rephrase a request that was perfectly clear."""
    from orderguard.intent_compiler import compile_intent
    from orderguard.llm import LLMUnavailable

    class _Down:
        name = "down"

        def complete(self, system, user, schema):
            raise LLMUnavailable("503 from provider")

    result = compile_intent(_Down(), user_request="two milk", intent_id="i", user_id="u")

    question = result.clarifications[0].question
    assert "could not reach the service" in question
    assert "Nothing was ordered" in question
    assert "understand" not in question.lower()
    assert "LLMUnavailable" in result.model_error
