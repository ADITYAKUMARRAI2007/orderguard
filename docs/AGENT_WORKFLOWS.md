# Agent Workflows

Who may touch what, and what they must report back.

---

## Roles

| Role | May edit | Scope | Must not |
|---|---|---|---|
| **Main** (integration owner) | yes | all | let an agent expand scope; accept "tests passed" without seeing which |
| **Architecture reviewer** | no | read-only | write implementation code unless asked |
| **Implementer** | yes | **only the listed files** | touch unrelated files; change frozen interfaces |
| **Test & adversarial** | tests only | `tests/` | weaken an assertion to make a test pass |
| **Security reviewer** | no | read-only | change business logic |
| **Simplicity reviewer** | no | read-only | add features |

---

## Handoff contract — every delegated task states all eleven

Task · Purpose · **Files in scope** · **Files that must NOT change** ·
Interfaces to preserve · Assumptions · Acceptance criteria · Tests required ·
Expected output · Safety invariants · Time budget

## Return contract — every agent reports all nine

1. Files inspected
2. Files changed
3. Why
4. Tests run
5. **Exact results** — not "passed"
6. Risks remaining
7. Assumptions made
8. Recommended next step
9. Anything it could NOT verify

**The main agent inspects the diff before accepting it.**

---

## Safety-critical rule

**Safety-critical** = anything touching order state, payment state, fulfilment,
refund recommendation, mandate enforcement, or idempotency.
That is **CP-4, CP-5, CP-6, CP-7**, and any later change to them.

Each requires two reviews **by different agents**:

**Review A — correctness:** state transitions · amount and currency handling ·
integer-paise usage · transaction boundaries · idempotency · retries ·
postcondition verification · race conditions · duplicate events · out-of-order events.

**Review B — adversarial:** find a case that could repair the wrong order · act
twice · fulfil a refunded order · fulfil an expired service · act on an
authorised-but-uncaptured payment · accept a currency mismatch · let the LLM
bypass a gate · lose the audit record · leave the database partially written.

> **The implementation pass never counts as verification.**
> If separate agents are unavailable, run two explicitly separated passes and
> **label them non-independent in `CHECKPOINTS.md`.** An unlabelled single pass
> masquerading as two reviews is worse than one honest review.

---

## Parallel work

**Safe to parallelise:** documentation and test design · security review and
README evidence checking · architecture diagram and API docs.

**Never parallelise:** two agents editing domain models · matcher and policy engine
before interfaces are frozen · schema and repository changes independently ·
an implementer reviewing its own work · two agents on the same file.

**Before any parallel run, define:** file ownership · shared interfaces ·
merge order · conflict policy · which result is authoritative.

**One agent may modify a given file at a time.**
