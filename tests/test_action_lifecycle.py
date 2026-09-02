"""Universal action-approval lifecycle. The one invariant that matters most:
R3 never resolves through this lifecycle at all — it is structurally routed
to the existing commerce payment path instead, not merely refused here as a
policy a future edit could quietly relax.
"""

import pytest

from orderguard.agent.lifecycle import ActionProposal, R3NeverEntersLifecycle, next_status


def test_r0_auto_executes():
    proposal = ActionProposal(proposal_id="p1", connector_id="github", capability="DEV_TASK", risk_tier="R0")
    assert next_status(proposal) == "EXECUTING"


def test_r1_waits_for_approval_by_default():
    proposal = ActionProposal(proposal_id="p2", connector_id="x", capability="EMAIL_DRAFT", risk_tier="R1")
    assert next_status(proposal) == "PROPOSED"
    assert next_status(proposal, user_approved=True) == "EXECUTING"


def test_r1_auto_executes_when_the_session_opted_in():
    proposal = ActionProposal(
        proposal_id="p3", connector_id="x", capability="EMAIL_DRAFT",
        risk_tier="R1", allow_r1_without_prompt=True,
    )
    assert next_status(proposal) == "EXECUTING"


def test_r2_always_requires_explicit_approval():
    proposal = ActionProposal(proposal_id="p4", connector_id="x", capability="CALENDAR_INVITE", risk_tier="R2")
    assert next_status(proposal) == "PROPOSED"
    assert next_status(proposal, user_approved=True) == "EXECUTING"


def test_r3_can_never_even_be_constructed():
    """The point of this module: an R3 ActionProposal is refused at
    construction, not merely at approval time — there is no lifecycle state
    a financial action can occupy here at all."""
    with pytest.raises(R3NeverEntersLifecycle):
        ActionProposal(proposal_id="p5", connector_id="shopify", capability="COMMERCE_PAYMENT", risk_tier="R3")


def test_a_terminal_status_is_not_recomputed():
    proposal = ActionProposal(
        proposal_id="p6", connector_id="x", capability="TASK", risk_tier="R0", status="SUCCEEDED",
    )
    assert next_status(proposal) == "SUCCEEDED"
