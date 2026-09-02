"""OrderGuard as a connector any assistant can plug into.

The scenario throughout is the one this was built for: two plates of chicken
momos from Zomato, under ₹400. We never call Zomato — the assistant does, using
the user's own account, and hands us the cart.
"""

import json

import pytest

from orderguard import mcp_server
from orderguard.audit import audit_engine
from orderguard.connector_log import connector_log_engine
from orderguard.mcp_server import PROTOCOL_VERSION, handle_rpc
from orderguard.memory import memory_engine


def call(tool: str, arguments: dict) -> dict:
    response = handle_rpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    })
    return json.loads(response["result"]["content"][0]["text"])


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """Each test gets its own in-memory audit trail, connector log, and
    memory — never the real data/*.db files — same isolation pattern as
    MEMORY/LEDGER in test_payment_flow.py."""
    monkeypatch.setattr(mcp_server, "AUDIT", audit_engine(":memory:"))
    monkeypatch.setattr(mcp_server, "CONNECTOR_LOG", connector_log_engine(":memory:"))
    monkeypatch.setattr(mcp_server, "MEMORY", memory_engine(":memory:"))


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


def test_all_tools_are_offered():
    tools = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == [
        "record_intent", "check_cart", "verify_audit_trail",
        "recommend_connector", "list_verified_connectors",
    ]


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
    assert result["checks_passed"] == result["checks_total"] == 13
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


# --- the audit trail: every call leaves a real, verifiable trace -----------

def test_recording_an_intent_leaves_an_audit_event(intent_id):
    trail = call("verify_audit_trail", {})
    assert trail["verified"] is True
    types = [e["event_type"] for e in trail["events"]]
    assert "intent_recorded" in types


def test_a_refused_intent_is_logged_with_the_same_weight_as_a_recorded_one():
    call("record_intent", {"user_request": "buy something", "items": []})
    trail = call("verify_audit_trail", {})
    assert any(e["event_type"] == "intent_refused" for e in trail["events"])


def test_an_allowed_and_a_blocked_cart_both_leave_a_cart_checked_event(intent_id):
    call("check_cart", _cart(intent_id))                          # allow=True
    call("check_cart", _cart(intent_id, quantity=20, line_total=338000))  # allow=False

    trail = call("verify_audit_trail", {})
    checked = [e for e in trail["events"] if e["event_type"] == "cart_checked"]
    assert [e["payload"]["allow"] for e in checked] == [True, False]
    assert checked[1]["payload"]["failed_gates"]  # names which gates fired


def test_a_cart_checked_against_no_intent_is_logged_as_a_refusal():
    call("check_cart", {
        "intent_id": "og_never_recorded", "merchant": "zomato",
        "lines": [], "total_paise": 0,
    })
    trail = call("verify_audit_trail", {})
    assert any(e["event_type"] == "cart_check_refused" for e in trail["events"])


def test_the_audit_trail_is_verified_by_recomputing_hashes_not_trusting_them():
    from sqlmodel import Session, select

    from orderguard.audit import AuditEvent

    call("record_intent", {
        "user_request": "milk", "items": [{"product": "milk", "quantity": 1}],
        "maximum_total_paise": 10000,
    })
    with Session(mcp_server.AUDIT) as db:
        row = db.exec(select(AuditEvent).where(AuditEvent.seq == 0)).one()
        row.payload_json = '{"tampered": true}'
        db.add(row)
        db.commit()

    trail = call("verify_audit_trail", {})
    assert trail["verified"] is False
    assert trail["broken_at_seq"] == 0


# --- connector recommendation and memory ------------------------------------

def test_recommending_a_connector_never_invents_one_outside_the_directory():
    result = call("recommend_connector", {"category": "grocery"})
    assert result["recommended"] in {"instacart"} or result["recommended"] is not None
    from orderguard.connectors import by_id
    by_id(result["recommended"])   # raises KeyError if it isn't a real, known connector


def test_a_category_with_no_cart_capable_connector_says_so_honestly():
    result = call("recommend_connector", {"category": "nonexistent-category"})
    assert result["recommended"] is None
    assert "no cart-capable connector known" in result["why"]


def test_a_missing_category_is_refused_not_guessed():
    result = call("recommend_connector", {"category": ""})
    assert result["recommended"] is None


def test_uber_eats_is_never_recommended_because_it_cannot_hand_back_a_cart():
    result = call("recommend_connector", {"category": "food"})
    assert result["recommended"] != "uber-eats"
    assert "uber-eats" not in (result.get("alternatives") or [])


def test_a_successful_check_makes_that_connector_the_remembered_recommendation(intent_id):
    call("check_cart", _cart(intent_id, merchant="zomato"))   # allow=True, per _cart() defaults

    result = call("recommend_connector", {"category": "food"})
    assert result["recommended"] == "zomato"
    assert result["remembered_preference"] is True


def test_an_unrecognised_merchant_teaches_nothing_about_its_category(intent_id):
    """test_the_same_code_checks_any_merchant proves check_cart works for
    merchants outside the directory. That must not silently make them
    recommendable — we would have no idea what category they even belong to."""
    call("check_cart", _cart(intent_id, merchant="some-shop-we-never-heard-of.example"))

    from orderguard.memory import saved_stores
    from orderguard.mcp_server import MEMORY, _USER_ID
    assert saved_stores(MEMORY, _USER_ID) == []


# --- parity with the web session flow ---------------------------------------
# The other purchase path (app.py's /api/sessions/*) runs the SAME thirteen
# named gates. This is what proves it, not just asserts it: the exact
# GateName that fires here is the exact GateName the checkout_guard test
# suite already proves for the same corrupted-cart shape, and both paths
# call the identical evaluate_pre_payment_gates function -- not two
# implementations that happen to agree today.

def test_check_cart_calls_the_identical_gate_function_the_web_session_uses():
    import inspect

    from orderguard.checkout_guard import evaluate_pre_payment_gates
    source = inspect.getsource(mcp_server._check_cart)
    assert "evaluate_pre_payment_gates(" in source
    # Not a second, similarly-named function -- the actual same object.
    assert mcp_server.evaluate_pre_payment_gates is evaluate_pre_payment_gates


def test_a_quantity_attack_via_mcp_fails_on_the_same_named_gate_as_the_web_flow(intent_id):
    """checkout_guard's own suite proves G_QUANTITIES_MATCH fires for 2
    approved vs 20 in the cart (see test_checkout_guard.py). Reproducing the
    identical shape through the MCP surface and asserting the SAME gate name
    fires is the actual parity proof -- an assistant using Claude/any MCP
    client gets no weaker a check than the web UI."""
    from orderguard.enums import GateName
    result = call("check_cart", _cart(intent_id, quantity=20, line_total=338000))
    assert not result["allow"]
    assert str(GateName.QUANTITIES_MATCH) in result["failed"]


def test_a_correct_mcp_cart_passes_the_exact_same_thirteen_named_gates_the_web_flow_does(intent_id):
    from orderguard.enums import PRE_PAYMENT_GATES
    result = call("check_cart", _cart(intent_id))
    assert result["allow"] is True
    assert result["checks_passed"] == len(PRE_PAYMENT_GATES) == 13


def test_the_mcp_surface_exposes_zero_payment_capable_tools():
    """The other half of the R3 invariant, proven at the MCP boundary
    specifically: this server can check a cart, never write or pay for one.
    A compromised assistant calling this server directly has nothing here
    that could move money even if it tried."""
    names = {t["name"] for t in mcp_server.TOOLS}
    forbidden_substrings = ("pay", "checkout", "charge", "capture", "refund", "order_create")
    for name in names:
        assert not any(bad in name.lower() for bad in forbidden_substrings), (
            f"{name!r} looks payment-capable and must never be exposed here"
        )


def test_list_verified_connectors_reports_evidence_and_capability_separately():
    result = call("list_verified_connectors", {})
    zomato = next(c for c in result["connectors"] if c["id"] == "zomato")
    uber_eats = next(c for c in result["connectors"] if c["id"] == "uber-eats")
    assert zomato["evidence"] == "restricted"
    assert zomato["capability"] == "cart_mutable"
    assert uber_eats["capability"] == "discovery_only"
    assert sum(result["summary"].values()) == len(result["connectors"])
