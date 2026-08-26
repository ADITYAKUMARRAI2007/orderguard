# Debugging Protocol

**When something fails, do not rewrite code.** Follow these ten steps in order.
Skipping to step 6 is how a symptom gets patched and the cause survives.

---

## The ten steps

### 1. Reproduce
Record all of it, not just the error:
- exact command
- input
- **seed**
- environment (python version, whether keys were set)
- expected result
- actual result
- **complete** error message, not the last line

### 2. Minimise
Reduce to the smallest failing case. One record, not fifty. One gate, not eleven.

### 3. Locate the boundary
Which component owns this?

```
generation → normalisation → matching → gate → idempotency →
transaction → demo-store adapter → Razorpay adapter → LLM adapter → scorer → UI
```

Naming the boundary before guessing the cause prevents fixing the wrong layer.

### 4. Hypothesise — fill the table, do not skip it

| Hypothesis | Evidence for | Evidence against | Test |
|---|---|---|---|

**Do not select a cause before testing it.** The column that matters is
"evidence against" — it is the one people leave blank.

### 5. Write a failing test
**A bug is not ready to fix until a test reproduces it.** If you cannot write
the test, you do not yet understand the bug.

### 6. Make the smallest fix
No unrelated refactoring. A bugfix commit that also tidies imports is two commits
pretending to be one, and it makes the next bisect useless.

### 7. Focused tests
Run the smallest relevant set first. Fast feedback.

### 8. Regression suite
Only after the focused test passes.

### 9. Explain the root cause in simple language
- what happened
- why it happened
- **why the existing tests missed it**
- how the fix prevents recurrence

The third one is the valuable question.

### 10. Record it in `FAILURE_LOG.md`
Written **as it happens**. Never reconstructed at the end — reconstructed entries
read as invented, because they are.

---

## Recurring traps in this project

**Shell state does not persist between command blocks.**
Every block re-sources `.env` and reloads state from disk. See F-001 — this one
already bit us before a line of code existed, and it fails *silently* with empty
strings rather than loudly.

**Money comparisons.** If an amount check behaves oddly, first confirm nothing has
become a `float`. Integer paise in, integer paise out.

**Idempotency.** If a duplicate slips through, check whether `cart_hash` was
recomputed instead of read from `confirmed_cart_hash` (D-004).

**Ground-truth leakage.** If match rates look suspiciously good, verify that
runtime code cannot read `data/truth/`. That test exists for a reason.

**Model output.** If something downstream receives a malformed object, the schema
validation was bypassed. Invalid model output must produce a clarification —
never a payment.

---

## What not to do

- Do not rerun and hope
- Do not fix without a reproducing test
- Do not combine a fix with a refactor
- Do not weaken an assertion to make a test pass — that is deleting the evidence
- Do not skip the failure log because the fix was small
