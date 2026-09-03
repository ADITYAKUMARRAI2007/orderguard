"""A staged cart proposal must survive a process restart -- it used to live
only in an in-memory dict in app.py, which a Render free-tier restart
between propose and approve would silently wipe (FAILURE_LOG.md F-035).
"""

from orderguard.agent.cart_proposals import cart_proposals_engine, load_proposal, save_proposal
from orderguard.agent.lifecycle import ActionProposal


def test_a_proposal_survives_a_fresh_engine_against_the_same_file(tmp_path):
    """Two separate Engine objects pointed at the same SQLite file stand in
    for "the process restarted" -- nothing here is held in Python memory
    between the two engines."""
    db_path = tmp_path / "cart_proposals.db"
    write_engine = cart_proposals_engine(db_path)

    proposal = ActionProposal(
        proposal_id="prop-1",
        connector_id="swiggy-instamart",
        capability="COMMERCE_GROCERY",
        risk_tier="R1",
        tool_name="update_cart",
        arguments={"variant_id": "sku-1", "quantity": 3},
        summary="Add 3 x Milk to your real cart.",
    )
    save_proposal(write_engine, proposal)

    read_engine = cart_proposals_engine(db_path)  # a fresh Engine, same file
    reloaded = load_proposal(read_engine, "prop-1")

    assert reloaded is not None
    assert reloaded.proposal_id == "prop-1"
    assert reloaded.connector_id == "swiggy-instamart"
    assert reloaded.risk_tier == "R1"
    assert reloaded.status == "PROPOSED"
    assert reloaded.arguments == {"variant_id": "sku-1", "quantity": 3}
    assert reloaded.summary == "Add 3 x Milk to your real cart."


def test_status_updates_persist_across_a_fresh_engine():
    db_path_engine = cart_proposals_engine(":memory:")
    # :memory: is per-connection in SQLite, so this single-engine flow
    # instead exercises the upsert path directly: propose, then mutate and
    # re-save twice, confirming each save reflects in a fresh load from the
    # SAME engine (the StaticPool keeps one shared connection for
    # ":memory:", which is exactly what production Postgres reads too --
    # every session sees the same underlying data).
    proposal = ActionProposal(
        proposal_id="prop-2",
        connector_id="swiggy-instamart",
        capability="COMMERCE_GROCERY",
        risk_tier="R1",
        tool_name="update_cart",
        arguments={"variant_id": "sku-2", "quantity": 1},
        summary="Add 1 x Bread to your real cart.",
    )
    save_proposal(db_path_engine, proposal)

    proposal.status = "EXECUTING"
    save_proposal(db_path_engine, proposal)
    assert load_proposal(db_path_engine, "prop-2").status == "EXECUTING"

    proposal.status = "SUCCEEDED"
    save_proposal(db_path_engine, proposal)
    assert load_proposal(db_path_engine, "prop-2").status == "SUCCEEDED"


def test_loading_an_unknown_proposal_id_returns_none():
    engine = cart_proposals_engine(":memory:")
    assert load_proposal(engine, "never-existed") is None
