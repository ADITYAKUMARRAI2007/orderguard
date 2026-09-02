"""Can we shop where the user asked? Answered before anything is searched.

Every case here came from a real broken session (F-015). "Order 2 pizza from
La Pinoz" asked for a budget, searched five grocery shops, and offered a
mozzarella block from an organic farm store.
"""

import pytest

from orderguard.merchants import Reach, resolve_merchant


# --- shops we can use -------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("named", [
    "Slurrp Farm", "slurrpfarm.com", "slurrp farm", "SLURRPFARM.COM", "slurrpfarm",
])
async def test_a_store_we_verified_is_shoppable_however_it_is_written(named):
    verdict = await resolve_merchant(named)
    assert verdict.can_shop
    assert verdict.domain == "slurrpfarm.com"


@pytest.mark.asyncio
async def test_our_own_demo_shop_is_a_real_merchant():
    """FreshCart is where the Razorpay test payment runs.

    A Shopify store collects its own money, so the payment demonstration needs
    a merchant that is genuinely ours.
    """
    verdict = await resolve_merchant("freshcart")
    assert verdict.can_shop
    assert verdict.label == "FreshCart"


@pytest.mark.asyncio
async def test_a_store_the_user_added_themselves_is_shoppable():
    verdict = await resolve_merchant(
        "Farmley", extra_domains=(("farmley.com", "Farmley"),)
    )
    assert verdict.can_shop
    assert verdict.domain == "farmley.com"


# --- shops we cannot use, and the difference between the reasons ------------

@pytest.mark.asyncio
async def test_swiggy_instamart_is_blocked_here_and_says_where_to_shop_it_instead():
    """As of 2026-08-29, Swiggy Instamart is genuinely connected and proven
    live — through Claude's own session, not through this app's own direct
    search. So this app still can't search it FOR you, but the reason has
    changed from "we have no access" to "that's not this app's job"."""
    verdict = await resolve_merchant("Swiggy Instamart")
    assert verdict.reach is Reach.BLOCKED
    assert not verdict.can_shop
    assert "cannot shop Swiggy Instamart" in verdict.message
    assert "connected Claude session" in verdict.message

    # a bare "Swiggy" no longer names exactly one connector — three real,
    # distinct surfaces exist now, and guessing which one would be wrong
    unqualified = await resolve_merchant("Swiggy")
    assert unqualified.reach is not Reach.BLOCKED


@pytest.mark.asyncio
async def test_zomato_is_blocked_by_their_rules():
    verdict = await resolve_merchant("zomato")
    assert verdict.reach is Reach.BLOCKED
    assert "Claude" in verdict.message         # you can use it there, not here


@pytest.mark.asyncio
async def test_zepto_has_no_agent_surface_at_all():
    verdict = await resolve_merchant("Zepto")
    assert verdict.reach is Reach.NOT_REACHABLE
    assert "do not use them" in verdict.message


@pytest.mark.asyncio
async def test_a_restaurant_nobody_has_an_api_for_is_refused_helpfully():
    """The one that started this. It must not become a grocery search."""
    verdict = await resolve_merchant("La Pinoz")

    assert verdict.reach is Reach.NOT_REACHABLE
    assert not verdict.can_shop
    assert "could not find a way to shop La Pinoz" in verdict.message
    # and it offers the two things we CAN do
    assert "website" in verdict.message
    assert "web" in verdict.message


@pytest.mark.asyncio
async def test_an_empty_name_is_not_a_refusal():
    """Naming no shop is the normal case: we search everything."""
    verdict = await resolve_merchant("")
    assert verdict.reach is Reach.UNKNOWN
    assert not verdict.can_shop


@pytest.mark.asyncio
async def test_a_domain_that_points_at_our_own_network_is_refused():
    verdict = await resolve_merchant("127.0.0.1")
    assert verdict.reach is Reach.NOT_REACHABLE
