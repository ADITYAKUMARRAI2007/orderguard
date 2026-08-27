"""OrderGuard as a connector any assistant can plug into.

The scenario throughout is the one this was built for: two plates of chicken
momos from Zomato, under ₹400. We never call Zomato — the assistant does, using
the user's own account, and hands us the cart.
"""

import json

import pytest

from orderguard.mcp_server import PROTOCOL_VERSION, handle_rpc


def call(tool: str, arguments: dict) -> dict:
    response = handle_rpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    })
    return json.loads(response["result"]["content"][0]["text"])


@pytest.fixture
def intent_id() -> str:
    return call("record_intent", {
        "user_request": "order two plates of chicken momos, under 400 rupees",
        "items": [{"product": "chicken momos", "quantity": 2, "unit": "plate"}],
        "maximum_total_paise": 40000,
    })["intent_id"]


def _cart(intent_id, quantity=2, line_total=33800, total=None, merchant="zomato"):
    return {
        "intent_id": intent_id, "merchant": merchant,
        "lines": [{
            "item_id": "dish_8871", "title": "Chicken Momos (8 pc)",
            "quantity": quantity, "line_total_paise": line_total,
        }],
        "total_paise": total if total is not None else line_total,
    }


# --- the protocol -----------------------------------------------------------

def test_it_speaks_mcp():
    result = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})["result"]
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in result["capabilities"]


def test_initialize_tells_the_assistant_the_order_to_call_things_in():
    """An assistant that calls check_cart first would be checking its own work."""
    instructions = handle_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    )["result"]["instructions"]
    assert "BEFORE shopping" in instructions
    assert "do not order or pay" in instructions.lower()


def test_both_tools_are_offered():
    tools = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["record_intent", "check_cart"]


def test_a_notification_gets_no_reply():
    assert handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_and_unknown_tool_are_rpc_errors():
    assert "error" in handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "nope"})
    assert "error" in handle_rpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "delete_everything", "arguments": {}},
    })


# --- recording what the user asked for --------------------------------------

def test_a_budget_is_never_invented():
    """The refusal that matters most.

    An agent that picks its own spending limit has granted itself permission
    the user never gave. There is no default, and no clever guess from the
    prices it happened to find.
    """
    result = call("record_intent", {
        "user_request": "order some momos",
        "items": [{"product": "chicken momos", "quantity": 2}],
    })
    assert result["recorded"] is False
    assert result["missing"] == ["maximum_total_paise"]
    assert "most you want to spend" in result["ask_the_user"]


def test_a_zero_or_negative_budget_is_not_a_budget():
    for cap in (0, -1):
        result = call("record_intent", {
            "user_request": "momos", "items": [{"product": "momos", "quantity": 1}],
            "maximum_total_paise": cap,
        })
        assert result["recorded"] is False


def test_an_empty_order_is_refused():
    result = call("record_intent", {
        "user_request": "buy something", "items": [], "maximum_total_paise": 40000,
    })
    assert result["recorded"] is False
    assert result["missing"] == ["items"]


# --- checking the cart ------------------------------------------------------

def test_a_correct_cart_passes_every_check(intent_id):
    result = call("check_cart", _cart(intent_id))
    assert result["allow"] is True
    assert result["checks_passed"] == result["checks_total"] == 12
    assert result["instruction"] == "Proceed to payment."


def test_twenty_plates_when_two_were_approved_is_blocked(intent_id):
    """The failure this whole project exists for."""
    result = call("check_cart", _cart(intent_id, quantity=20, line_total=338000))

    assert result["allow"] is False
    assert "Do NOT order or pay" in result["instruction"]
    assert any("quantities differ" in r for r in result["reasons"])
    assert result["cart_contains"].startswith("20 item")


def test_delivery_fees_pushing_it_over_the_limit_are_caught(intent_id):
    """The cart lines are fine; the total is not. ₹398 of food, ₹456 to pay."""
    result = call("check_cart", _cart(intent_id, line_total=39800, total=45600))
    assert result["allow"] is False
    assert any("exceeds the approved spending cap" in r for r in result["reasons"])


def test_a_cart_with_no_recorded_intent_is_refused(intent_id):
    """Without an intent recorded first, there is nothing to check against."""
    result = call("check_cart", _cart("og_never_recorded"))
    assert result["allow"] is False
    assert "record_intent first" in result["reasons"][0]


def test_the_same_code_checks_any_merchant(intent_id):
    """Zomato and a Shopify shop are indistinguishable here, which is the point.

    OrderGuard checks carts, not stores, so a merchant nobody has integrated
    is supported the moment an assistant can reach it.
    """
    for merchant in ("zomato", "slurrpfarm.com", "some-shop-we-never-heard-of.example"):
        assert call("check_cart", _cart(intent_id, merchant=merchant))["allow"] is True


def test_a_malformed_cart_blocks_rather_than_crashing(intent_id):
    result = call("check_cart", {
        "intent_id": intent_id, "merchant": "zomato",
        "lines": [{"item_id": "x", "quantity": "two", "line_total_paise": 100}],
        "total_paise": 100,
    })
    assert result["allow"] is False


def test_a_cart_with_no_merchant_or_total_is_blocked(intent_id):
    assert call("check_cart", {
        "intent_id": intent_id, "merchant": "", "lines": [], "total_paise": 0,
    })["allow"] is False


# --- honesty ----------------------------------------------------------------

def test_a_block_is_not_reported_as_a_broken_call(intent_id):
    """``isError`` would invite a client to retry. A refusal is the right answer.

    This distinction decides whether an assistant treats a blocked cart as
    "try again" or as "stop and tell the user".
    """
    response = handle_rpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "check_cart",
                   "arguments": _cart(intent_id, quantity=20, line_total=338000)},
    })
    assert response["result"]["isError"] is False


def test_it_says_which_checks_it_cannot_perform(intent_id):
    """We are not in the payment path here, so we cannot promise no double charge.

    Claiming a check we did not run would be the worst kind of dishonesty in a
    safety product.
    """
    result = call("check_cart", _cart(intent_id))
    unchecked = " ".join(result["not_checked_here"])
    assert "duplicate payment" in unchecked
    assert "not in your payment path" in unchecked
