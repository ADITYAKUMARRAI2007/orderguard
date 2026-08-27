"""The connector directory has to stay honest as it grows.

These tests are mostly about what the directory is not allowed to claim.
"""

from orderguard.connectors import (
    CONNECTORS,
    Connector,
    Status,
    by_id,
    live_connectors,
    summary,
)


def test_every_connector_carries_evidence_and_a_date():
    """A status with no evidence behind it is an opinion."""
    for connector in CONNECTORS:
        assert connector.evidence.strip(), connector.id
        assert connector.checked_on.count("-") == 2, connector.id
        assert len(connector.evidence) > 30, f"{connector.id}: evidence is too thin"


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


def test_swiggy_is_recorded_as_real_but_out_of_reach():
    """I twice claimed Swiggy had nothing. It does. The record has to say so."""
    swiggy = by_id("swiggy")
    assert swiggy.status is Status.NEEDS_ACCESS      # not "unavailable"
    assert "401" in swiggy.evidence                  # the endpoint answered
    assert not swiggy.can_order


def test_zomato_is_excluded_by_their_rules_not_by_our_ability():
    zomato = by_id("zomato")
    assert zomato.status is Status.RESTRICTED
    assert "prohibited" in zomato.evidence.lower()


def test_unavailable_platforms_say_why_we_refuse_the_unofficial_route():
    """Zepto and friends have reverse-engineered servers. Saying no is the point."""
    for connector in CONNECTORS:
        if connector.status is Status.UNAVAILABLE:
            assert "do not use them" in connector.note
            assert "real money" in connector.note


def test_live_connectors_are_the_shopify_stores():
    live = live_connectors()
    assert len(live) >= 5
    assert all(c.status is Status.LIVE for c in live)
    assert all(c.protocol.startswith("Shopify") for c in live)
    assert all("never complete a purchase" in c.note for c in live)


def test_live_grocery_stores_can_be_filtered():
    grocery = live_connectors(kind="grocery")
    assert grocery
    assert all(c.kind == "grocery" for c in grocery)


def test_payments_is_not_offered_as_a_shop():
    assert all(c.kind != "payments" for c in live_connectors())


def test_ids_are_unique():
    ids = [c.id for c in CONNECTORS]
    assert len(ids) == len(set(ids))


def test_summary_counts_every_connector():
    assert sum(summary().values()) == len(CONNECTORS)


def test_a_connector_is_immutable():
    """A NamedTuple, so a status cannot be edited at runtime into a nicer one."""
    connector = by_id("swiggy")
    try:
        connector.status = Status.LIVE          # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("connector status should not be writable")
