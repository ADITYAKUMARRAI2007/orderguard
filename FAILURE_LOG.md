# Failure Log

Written **as failures happen**. Never reconstructed from memory afterwards.
This file is part of the buildathon submission — the application asks
"what broke, and how you got out," and Razorpay says it is the answer they read first.

---

## Template

```
# F-000
Date:
Checkpoint:
Command:
Seed:

## What I expected
## What happened
## Root cause
## How I proved the root cause
## Fix
## Regression test added
## What I learned
## Could this happen in production?
## Remaining limitation
```

---

## F-001 — Shell variables did not survive between command blocks

**Date:** 2026-08-26
**Checkpoint:** CP-0 (caught in review, before execution)
**Command:** planned Step 6 — `sed "s/__ORDER_ID__/$OID/; s/__KEY__/$RZP_KEY_ID/"`

### What I expected
`$OID` from Step 5 and `$RZP_KEY_ID` from `.env` to be available in Step 6.

### What happened
Caught during plan review, not at runtime. Each command block runs in a fresh shell,
so `$OID` was gone and Step 6 never sourced `.env` at all — **both** variables would
have been empty. The checkout page would have rendered with blank `order_id` and `key`
and failed confusingly rather than loudly.

### Root cause
Assumed shell state persists across separate command invocations. It does not.

### Fix
Step 5 persists the order ID to `probe/order_id.txt`. Step 6 re-sources `.env`
and reloads the file with `tr -d '\n' < probe/order_id.txt`.

### Regression test
Not code — a documented rule: **every command block re-sources `.env` and reloads
state from disk.** Applies to every checkpoint.

### What I learned
Silent empty-string substitution is worse than a hard error. Prefer commands that
fail loudly on missing state.

### Could this happen in production?
Yes — the same class of bug appears in CI pipelines and shell-based deploys.

### Remaining limitation
`probe/` is gitignored, so the order ID is not reproducible from a fresh clone.
Acceptable: it is a throwaway probe artefact.
