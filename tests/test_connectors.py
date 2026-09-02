"""The connector directory has to stay honest as it grows.

These tests are mostly about what the directory is not allowed to claim.
Evidence and capability are checked as the two independent questions they
are (2026-08-29 correction) — a connector can be real and untested by us
while still being unable to hand back a cart at all, and the tests must not
let those two facts collapse back into one.
"""

from orderguard.connectors import (
    CONNECTORS,
    Capability,
    Connector,
    Evidence,
    by_id,
    cart_capable_connectors,
    live_connectors,
    summary,
)


def test_every_connector_carries_evidence_and_a_date():
    """A status with no evidence behind it is an opinion."""
    for connector in CONNECTORS:
        assert connector.evidence_note.strip(), connector.id
        assert connector.checked_on.count("-") == 2, connector.id
        assert len(connector.evidence_note) > 30, f"{connector.id}: evidence is too thin"


def test_no_connector_claims_it_can_place_a_third_party_order():
    """The standing rule: we never complete a purchase on someone else's store.

    Razorpay is the single exception, and only in test mode, because that is our
    own merchant account rather than a third party's.
    """
    for connector in CONNECTORS:
        if connector.id == "razorpay":
            assert "test mode" in connector.note.lower()
            continue
        assert not connector.can_order, f"{connector.id} claims it can order"


def test_swiggy_is_now_connector_verified_not_merely_reachable():
    """2026-08-29: went from RESTRICTED (401, no credentials) to actually
    connected — a live, authenticated Claude Code MCP session, real
    record_intent/check_cart round trips, real audit-chain entries. Split
    into three ids because they are three separate MCP servers, not one."""
    instamart = by_id("swiggy-instamart")
    assert instamart.evidence is Evidence.CONNECTOR_VERIFIED
    assert instamart.capability is Capability.CART_MUTABLE
    assert "check_cart" in instamart.evidence_note
    assert not instamart.can_order        # verified via the guard, not ours to complete

    food = by_id("swiggy-food")
    assert food.evidence is Evidence.CONNECTOR_VERIFIED
    assert food.capability is Capability.CART_MUTABLE

    dineout = by_id("swiggy-dineout")
    assert dineout.evidence is Evidence.CONNECTOR_VERIFIED   # authentication genuinely works
    assert dineout.capability is Capability.DISCOVERY_ONLY   # but search returned nothing usable
    assert "empty result" in dineout.evidence_note


def test_zomato_is_excluded_by_their_rules_not_by_our_ability():
    """Real, live, reachable from Claude — and still not ours to use.

    An earlier version of this entry claimed no public endpoint existed. That
    was wrong (F-012): the endpoint is documented and answers 401. The right
    conclusion for the wrong reason is still a defect, because the reason is
    what the next reader acts on.
    """
    zomato = by_id("zomato")
    assert zomato.evidence is Evidence.RESTRICTED
    assert zomato.capability is Capability.CART_MUTABLE
    assert zomato.endpoint == "https://mcp-server.zomato.com/mcp"
    assert "401" in zomato.evidence_note            # it answered; it exists
    assert "not allowing any third party apps" in zomato.evidence_note
    # the refusal is quoted from a maintainer, not inferred from a README
    assert "issue #35" in zomato.evidence_note
    assert "localhost" in zomato.evidence_note      # the exact block on OrderGuard
    assert zomato.in_assistant_directory            # a person CAN use it in Claude
    assert not zomato.can_order                     # OrderGuard cannot


def test_uber_eats_is_capability_limited_not_just_unverified():
    """The correction: Uber Eats is real and in the directory, same as
    Instacart, but cannot be the second live check_cart proof because it
    never exposes a cart to Claude at all — checkout happens in Uber's own
    app. Evidence alone (AVAILABLE_UNTESTED) would have hidden this; it takes
    the separate capability field to say it."""
    uber_eats = by_id("uber-eats")
    assert uber_eats.evidence is Evidence.AVAILABLE_UNTESTED
    assert uber_eats.capability is Capability.DISCOVERY_ONLY
    assert uber_eats.in_assistant_directory
    assert "checkout in the Uber Eats app" in uber_eats.evidence_note


def test_instacart_and_cash_app_are_the_recommended_next_proof_targets():
    """Both are real, official, and genuinely cart-capable — the two
    concrete facts that make them better second-proof candidates than
    chasing more Swiggy/Zomato access."""
    for connector_id in ("instacart", "cash-app-orders"):
        connector = by_id(connector_id)
        assert connector.evidence is Evidence.AVAILABLE_UNTESTED
        assert connector.capability is Capability.CART_MUTABLE
        assert connector.in_assistant_directory


def test_reachable_in_claude_is_not_the_same_as_usable_by_us():
    """Two different questions. Collapsing them produced a wrong entry once.

    A connector a person can add to Claude is not automatically one our own
    application may connect to: Zomato whitelists redirect URIs, and ours is
    not among them.
    """
    for connector in CONNECTORS:
        if connector.in_assistant_directory:
            assert not connector.can_order, connector.id


def test_every_gated_connector_names_its_endpoint():
    """If we say a thing exists, we say where, so it can be re-checked."""
    for connector in CONNECTORS:
        if connector.evidence in (Evidence.RESTRICTED, Evidence.AVAILABLE_UNTESTED):
            assert connector.endpoint.startswith("https://"), connector.id


def test_unavailable_platforms_say_why_we_refuse_the_unofficial_route():
    """Zepto and friends have reverse-engineered servers. Saying no is the point."""
    for connector in CONNECTORS:
        if connector.evidence is Evidence.UNAVAILABLE:
            assert connector.capability is Capability.UNKNOWN
            assert "do not use them" in connector.note
            assert "real money" in connector.note


def test_live_connectors_are_the_shopify_stores():
    live = live_connectors()
    assert len(live) >= 5
    assert all(c.evidence is Evidence.DIRECT_VERIFIED for c in live)
    assert all(c.protocol.startswith("Shopify") for c in live)
    assert all("never complete a purchase" in c.note for c in live)


def test_live_grocery_stores_can_be_filtered():
    grocery = live_connectors(kind="grocery")
    assert grocery
    assert all(c.kind == "grocery" for c in grocery)


def test_payments_is_not_offered_as_a_shop():
    assert all(c.kind != "payments" for c in live_connectors())


def test_cart_capable_connectors_include_untested_ones():
    """The pool recommend_connector() should draw from is broader than just
    live_connectors() — it includes real, cart-capable connectors we simply
    haven't personally tried yet, like Instacart."""
    capable = cart_capable_connectors()
    ids = {c.id for c in capable}
    assert "instacart" in ids
    assert "cash-app-orders" in ids
    assert "uber-eats" not in ids          # discovery-only, correctly excluded
    assert all(c.capability is Capability.CART_MUTABLE for c in capable)
    assert all(c.kind != "payments" for c in capable)


def test_cart_capable_connectors_can_be_filtered_by_kind():
    grocery = cart_capable_connectors(kind="grocery")
    assert grocery
    assert all(c.kind == "grocery" for c in grocery)


def test_ids_are_unique():
    ids = [c.id for c in CONNECTORS]
    assert len(ids) == len(set(ids))


def test_summary_counts_every_connector():
    assert sum(summary().values()) == len(CONNECTORS)


def test_a_connector_is_immutable():
    """A NamedTuple, so a status cannot be edited at runtime into a nicer one."""
    connector = by_id("swiggy-instamart")
    try:
        connector.evidence = Evidence.DIRECT_VERIFIED          # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("connector evidence should not be writable")
