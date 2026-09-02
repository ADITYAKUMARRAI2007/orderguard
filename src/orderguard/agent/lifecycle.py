"""Universal action-approval lifecycle, generalized beyond commerce per an
explicit build correction: Commerce Missions (``missions.py``) are the
commerce-specific instance of this lifecycle, not a parallel concept.

    R0 (read)                  -> auto-executes
    R1 (reversible write)      -> executes only if the session has opted in
                                   (off by default)
    R2 (external commitment)   -> requires one explicit user approval
    R3 (financial)             -> NEVER resolves through this lifecycle.

The R3 line above is not a gap to fill in later — it is the point. A
financial action only ever moves through the existing, unmodified
``select_offer -> confirm -> gates -> Authorization -> payment`` path in
``app.py``. Nothing in this module, and nothing in ``agent/tools.py``'s
tool-list construction, is capable of producing a payment on its own; see
``tests/test_action_lifecycle.py`` for the assertion that R3 is structurally
routed elsewhere rather than merely refused here as a policy choice that a
future edit could quietly relax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RiskTier = Literal["R0", "R1", "R2", "R3"]
ProposalStatus = Literal["PROPOSED", "APPROVED", "EXECUTING", "SUCCEEDED", "FAILED"]

__all__ = ["ActionProposal", "R3NeverEntersLifecycle", "next_status"]


class R3NeverEntersLifecycle(RuntimeError):
    """Raised if anything ever tries to construct an R3 ``ActionProposal``.
    R3 actions are commerce payments, and commerce payments have their own
    path (``select_offer`` -> ... -> ``payment/verify``) that this lifecycle
    must never shadow, duplicate, or offer a second route into.
    """


@dataclass
class ActionProposal:
    proposal_id: str
    connector_id: str
    capability: str
    risk_tier: RiskTier
    status: ProposalStatus = "PROPOSED"
    allow_r1_without_prompt: bool = False
    # What executing this proposal actually does, once approved. Carried
    # here (not re-derived at approval time) so the action that executes is
    # provably the exact one the user saw and approved — never a fresh
    # decision made after the fact.
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    summary: str = ""

    def __post_init__(self) -> None:
        if self.risk_tier == "R3":
            raise R3NeverEntersLifecycle(
                f"refused to create an ActionProposal for R3 capability "
                f"{self.capability!r} on connector {self.connector_id!r} — "
                "financial actions only ever move through the payment path"
            )


def next_status(proposal: ActionProposal, *, user_approved: bool = False) -> ProposalStatus:
    """R0 auto-executes. R1 executes only if the session opted in
    (``allow_r1_without_prompt``); otherwise it waits for the same explicit
    approval R2 always requires. R2 always requires ``user_approved``.
    """
    if proposal.status != "PROPOSED":
        return proposal.status
    if proposal.risk_tier == "R0":
        return "EXECUTING"
    if proposal.risk_tier == "R1" and proposal.allow_r1_without_prompt:
        return "EXECUTING"
    return "EXECUTING" if user_approved else "PROPOSED"
