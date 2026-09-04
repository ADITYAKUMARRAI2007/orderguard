"""Real Swiggy Instamart cart writes — mocked at the MCP call boundary, so
this suite stays offline like everything else in this project. Two
invariants matter most: update_cart's real schema REPLACES the whole cart,
so a write must never happen without first reading and preserving whatever
else is already there; and update_cart's own "no error" reply is never
trusted as proof of what actually landed — a real, independent get_cart
read-back after the write is required before this module reports success.
"""

from unittest.mock import AsyncMock, patch

import pytest

from orderguard.agent.mcp_direct_client import DirectMcpCallError
from orderguard.agent.swiggy_cart import SwiggyCartError, add_to_instamart_cart


def _stateful_server(initial_items: list[dict]) -> tuple[dict, object]:
    """A minimal fake Swiggy backend: get_cart returns whatever the last
    update_cart actually wrote, so a post-write read-back genuinely
    reflects the write -- the same way the real server would."""
    state = {"items": list(initial_items)}

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"cartTotalAmount": "0", "items": list(state["items"])}
        state["items"] = list(arguments["items"])
        return {"written": arguments}

    return state, fake_call


async def test_adding_to_an_empty_cart_writes_only_the_new_item():
    _, fake_call = _stateful_server([])
    calls = []

    async def tracking_call(*, url, bearer_token, tool_name, arguments):
        calls.append((tool_name, arguments))
        return await fake_call(url=url, bearer_token=bearer_token, tool_name=tool_name, arguments=arguments)

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=tracking_call):
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="addr1", spin_id="SPIN1", quantity=1,
        )

    assert calls[0] == ("get_cart", {})
    assert calls[1] == (
        "update_cart",
        {"selectedAddressId": "addr1", "items": [{"spinId": "SPIN1", "quantity": 1}]},
    )
    assert calls[2] == ("get_cart", {})
    assert result["preserved_existing_items"] == 0


async def test_adding_to_a_populated_cart_preserves_the_existing_items():
    """The core safety property: update_cart REPLACES the cart, so a naive
    single-item write would silently delete whatever else was in it."""
    _, fake_call = _stateful_server([
        {"spinId": "EXISTING1", "quantity": 2, "displayName": "Bread"},
        {"spinId": "EXISTING2", "quantity": 1, "displayName": "Eggs"},
    ])
    calls = []

    async def tracking_call(*, url, bearer_token, tool_name, arguments):
        calls.append((tool_name, arguments))
        return await fake_call(url=url, bearer_token=bearer_token, tool_name=tool_name, arguments=arguments)

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=tracking_call):
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="addr1", spin_id="NEWSPIN", quantity=3,
        )

    write_call = calls[1]
    written_items = write_call[1]["items"]
    spin_ids = {item["spinId"] for item in written_items}
    assert spin_ids == {"EXISTING1", "EXISTING2", "NEWSPIN"}
    assert result["preserved_existing_items"] == 2


async def test_adding_the_same_item_again_updates_quantity_not_duplicates_the_line():
    _, fake_call = _stateful_server([{"spinId": "SPIN1", "quantity": 1}])
    calls = []

    async def tracking_call(*, url, bearer_token, tool_name, arguments):
        calls.append((tool_name, arguments))
        return await fake_call(url=url, bearer_token=bearer_token, tool_name=tool_name, arguments=arguments)

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=tracking_call):
        await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="SPIN1", quantity=5)

    written_items = calls[1][1]["items"]
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
    cart_read_skipped_reason, not silently claimed as a normal empty cart.
    Once update_cart re-anchors the cart to the newly picked, serviceable
    address, the post-write read-back succeeds normally."""
    calls = {"get_cart": 0}

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            calls["get_cart"] += 1
            if calls["get_cart"] == 1:
                raise DirectMcpCallError(
                    "get_cart returned an error: The selected address is not "
                    "serviceable at the moment. Please choose a different delivery address."
                )
            return {"items": [{"spinId": "NEW", "quantity": 1}]}
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call) as mock:
        result = await add_to_instamart_cart(
            bearer_token="tok", address_id="work-addr", spin_id="NEW", quantity=1,
        )

    write_call = mock.call_args_list[1]
    assert write_call.kwargs["arguments"] == {
        "selectedAddressId": "work-addr", "items": [{"spinId": "NEW", "quantity": 1}],
    }
    assert result["preserved_existing_items"] == 0
    assert result["cart_read_skipped_reason"] is not None
    assert "not serviceable" in result["cart_read_skipped_reason"]


async def test_a_normal_successful_read_never_sets_the_skipped_reason():
    _, fake_call = _stateful_server([])

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
    calls = {"get_cart": 0}

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            calls["get_cart"] += 1
            if calls["get_cart"] == 1:
                return {"items": [
                    {"spinId": "GOOD", "quantity": 1},
                    {"spinId": None, "quantity": 2},
                    {"spinId": "NOQTY"},
                ]}
            return {"items": [{"spinId": "GOOD", "quantity": 1}, {"spinId": "NEW", "quantity": 1}]}
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call) as mock:
        await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="NEW", quantity=1)

    written_items = mock.call_args_list[1].kwargs["arguments"]["items"]
    spin_ids = {item["spinId"] for item in written_items}
    assert spin_ids == {"GOOD", "NEW"}


async def test_update_cart_reporting_no_error_but_the_item_never_landing_fails_closed():
    """Real, reproduced incident (2026-09-04, see this module's docstring):
    update_cart returned no error, the UI said "CART UPDATED", and the real
    Swiggy site showed nothing added. update_cart's own reply is not proof
    of anything -- only an independent read-back is. Here the read-back
    never shows the item, so this must fail loudly instead of lying."""
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"items": []}  # never actually reflects the write
        return {"written": arguments}  # update_cart itself claims success

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError, match="not actually in the real cart"):
            await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="S1", quantity=1)


async def test_the_read_back_confirmation_call_itself_failing_fails_closed():
    """A write that can't even be confirmed is not a success -- this must
    not be conflated with the pre-write "unserviceable, treat as empty"
    allowance, which only ever applies before anything has been written."""
    calls = {"get_cart": 0}

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            calls["get_cart"] += 1
            if calls["get_cart"] == 1:
                return {"items": []}
            raise DirectMcpCallError("connection reset")
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError, match="could not be.*read back"):
            await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="S1", quantity=1)


async def test_a_write_that_wipes_previously_added_items_is_reported_not_hidden():
    """Real, reproduced live (2026-09-04): approving onion succeeded and was
    confirmed present; approving potato afterwards failed, and the onion was
    gone from the real cart too. Swiggy's own reply to a write containing an
    unsellable item (captured in F-036) is "no valid items remained, so the
    cart is now empty" -- it destroys the items the write was supposed to be
    preserving. A user must be told that, not left to find it on the
    merchant's own site."""
    calls = {"get_cart": 0}

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            calls["get_cart"] += 1
            if calls["get_cart"] == 1:
                return {"items": [{"spinId": "ONION", "quantity": 1}]}
            return {"items": []}  # Swiggy emptied the whole cart
        return {"message": "no valid items remained, so the cart is now empty"}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError, match="already in your cart"):
            await add_to_instamart_cart(
                bearer_token="tok", address_id="a1", spin_id="POTATO", quantity=1,
            )


async def test_an_item_landing_but_the_rest_of_the_cart_being_wiped_still_fails():
    """The nastier half: the new item IS there, so the naive check passes,
    but everything that was in the cart before is gone. Reporting this as a
    clean success would be exactly the false confidence F-048 fixed."""
    calls = {"get_cart": 0}

    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            calls["get_cart"] += 1
            if calls["get_cart"] == 1:
                return {"items": [{"spinId": "MILK", "quantity": 1}, {"spinId": "ONION", "quantity": 1}]}
            return {"items": [{"spinId": "APPLE", "quantity": 1}]}  # only the new one survived
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError, match="removed 2 item"):
            await add_to_instamart_cart(
                bearer_token="tok", address_id="a1", spin_id="APPLE", quantity=1,
            )


async def test_a_quantity_mismatch_on_read_back_also_fails_closed():
    """Confirms the read-back checks quantity, not just presence -- Swiggy
    silently capping quantity (stock limits, MOV rules, etc.) must not be
    reported as the exact write the user approved."""
    async def fake_call(*, url, bearer_token, tool_name, arguments):
        if tool_name == "get_cart":
            return {"items": [{"spinId": "S1", "quantity": 1}]}  # asked for 5, only 1 landed
        return {"written": arguments}

    with patch("orderguard.agent.swiggy_cart.call_tool_directly", side_effect=fake_call):
        with pytest.raises(SwiggyCartError, match="not actually in the real cart"):
            await add_to_instamart_cart(bearer_token="tok", address_id="a1", spin_id="S1", quantity=5)
