"""Real Swiggy Instamart cart writes — mocked at the MCP call boundary, so
this suite stays offline like everything else in this project. The one
invariant that matters most: update_cart's real schema REPLACES the whole
cart, so a write must never happen without first reading and preserving
whatever else is already there.
"""

from unittest.mock import AsyncMock, patch

import pytest

from orderguard.agent.mcp_direct_client import DirectMcpCallError
from orderguard.agent.swiggy_cart import SwiggyCartError, add_to_instamart_cart


async def test_adding_to_an_empty_cart_writes_only_the_new_item():
    calls = []

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        calls.append((tool_name, arguments))
        if tool_name == "get_cart":
            return {"cartTotalAmount": "0", "items": [], "cartAbsent": True}
        return {"cartTotalAmount": "108", "items": arguments["items"]}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="addr1", spin_id="SPIN1", quantity=1,
        )

    assert calls[0] == ("get_cart", {})
    assert calls[1] == (
        "update_cart",
        {"selectedAddressId": "addr1", "items": [{"spinId": "SPIN1", "quantity": 1}]},
    )
    assert result["preserved_existing_items"] == 0


async def test_adding_to_a_populated_cart_preserves_the_existing_items():
    """The core safety property: update_cart REPLACES the cart, so a naive
    single-item write would silently delete whatever else was in it."""
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {
                "cartTotalAmount": "150",
                "items": [
                    {"spinId": "EXISTING1", "quantity": 2, "displayName": "Bread"},
                    {"spinId": "EXISTING2", "quantity": 1, "displayName": "Eggs"},
                ],
            }
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call) as mock:
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="addr1", spin_id="NEWSPIN", quantity=3,
        )

    write_call = mock.call_args_list[1]
    written_items = write_call.kwargs["arguments"]["items"]
    spin_ids = {item["spinId"] for item in written_items}
    assert spin_ids == {"EXISTING1", "EXISTING2", "NEWSPIN"}
    assert result["preserved_existing_items"] == 2


async def test_adding_the_same_item_again_updates_quantity_not_duplicates_the_line():
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"items": [{"spinId": "SPIN1", "quantity": 1}]}
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call) as mock:
        await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="SPIN1", quantity=5)

    written_items = mock.call_args_list[1].kwargs["arguments"]["items"]
    assert written_items == [{"spinId": "SPIN1", "quantity": 5}]


async def test_a_read_failure_before_writing_fails_closed_never_blind_writes():
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        raise DirectMcpCallError("connection reset")

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError):
            await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="S1", quantity=1)


async def test_an_unserviceable_cart_address_is_treated_as_empty_not_blocked_forever():
    """Regression for a real, reproduced incident: get_cart takes no address
    argument at all (verified against its live tool schema) — it reads
    whatever address the account's cart is CURRENTLY anchored to, which can
    be stale. On a real account whose cart was last anchored to an address
    Instamart no longer serves, get_cart itself failed with "The selected
    address is not serviceable at the moment," even though search_products
    and update_cart both worked fine against the newly picked, genuinely
    serviceable address (reproduced live). A cart anchored to an
    unserviceable address holds nothing deliverable to the new address
    either way, so this specific failure is treated as "nothing to
    preserve" and the write proceeds — honestly flagged via
    cart_read_skipped_reason, not silently claimed as a normal empty cart."""
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            raise DirectMcpCallError(
                "get_cart returned an error: The selected address is not "
                "serviceable at the moment. Please choose a different delivery address."
            )
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call) as mock:
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="work-addr", spin_id="NEW", quantity=1,
        )

    write_call = mock.call_args_list[-1]
    assert write_call.kwargs["arguments"] == {
        "selectedAddressId": "work-addr", "items": [{"spinId": "NEW", "quantity": 1}],
    }
    assert result["preserved_existing_items"] == 0
    assert result["cart_read_skipped_reason"] is not None
    assert "not serviceable" in result["cart_read_skipped_reason"]


async def test_a_normal_successful_read_never_sets_the_skipped_reason():
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"items": []}
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="a1", spin_id="S1", quantity=1,
        )

    assert result["cart_read_skipped_reason"] is None


async def test_a_write_failure_is_reported_not_swallowed():
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"items": []}
        raise DirectMcpCallError("server rejected the write")

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError):
            await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="S1", quantity=1)


async def test_a_missing_or_malformed_existing_item_is_skipped_not_guessed():
    """A cart line missing spinId or quantity must never be silently dropped
    from the merge by inventing a value for it -- it is simply excluded,
    which is safer than guessing wrong and either losing or duplicating a
    real item."""
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"items": [
                {"spinId": "GOOD", "quantity": 1},
                {"spinId": None, "quantity": 2},
                {"spinId": "NOQTY"},
            ]}
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call) as mock:
        await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="NEW", quantity=1)

    written_items = mock.call_args_list[1].kwargs["arguments"]["items"]
    spin_ids = {item["spinId"] for item in written_items}
    assert spin_ids == {"GOOD", "NEW"}
