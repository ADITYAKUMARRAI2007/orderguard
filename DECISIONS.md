# Decisions

Every entry: decision · context · alternatives · chosen · reason · downside · evidence · when to revisit.

---

## D-000 — Workspace path has a trailing space

**Date:** 2026-08-26
**Context:** `PWD` is `/Users/adityakumarrai/razorpay ` with a trailing space. `~/razorpay` does not exist. `mkdir -p ~/razorpay` would silently create a *second*, wrong directory and split the project.
**Decision:** every command uses `"$PWD"`. Never the tilde form.
**Evidence:** `test -d "$HOME/razorpay"` → missing; `test -d "$HOME/razorpay "` → exists.
**Revisit:** never.

## D-001 — Track 01 primary, Track 04 fallback from the same core

**Context:** one system can satisfy either bar; the form accepts one track.
**Alternatives:** (a) Track 04 only (b) Track 01 only (c) shared core, decide on evidence.
**Chosen:** (c), Track 01 primary.
**Reason:** CP1–CP7 give Track 04 its required components, so a submittable project should exist from 2 Sep. CP8–CP9 are additive.
**NOT unconditional:** Track 04 is cleared only when CP-7 actually reports the metric set in D-010. Until those numbers exist, the fallback is **not** secured.
**Downside:** two README variants (~half a day).
**Revisit:** CP-9, on working evidence.

## D-002 — Money as integer paise

**Reason:** float rounding in payments is disqualifying. `Decimal` only at display and parsing boundaries.
**Downside:** conversion boilerplate at every boundary. Accepted.

## D-003 — SQLModel over plain SQLAlchemy

**Date:** 2026-08-26 · **Decided**
**Reason:** Pydantic + SQLAlchemy in one declaration; roughly halves the model code, and models are the CP-1 deliverable so this had to be settled first.
**Downside:** thinner escape hatch for exotic SQL. Irrelevant at this scale.

## D-004 — `cart_hash` computed once at confirmation

**Context:** recomputing the hash per attempt produces a different idempotency key on retry, and idempotency silently fails.
**Chosen:** hash frozen at user confirmation, stored as `confirmed_cart_hash`, reused by every retry.
**Downside:** an edited cart becomes a new intent needing new confirmation. Correct, but must be explained in the README.

## D-005 — `purchase_intent_id` is the correlation key

**Context:** agent purchases may carry no customer email, so customer match cannot be a required gate.
**Correlation path:**
```
payment.order_id → fetch Razorpay order → order.notes.purchase_intent_id → PurchaseIntent
```
**Note:** order notes are **not** assumed to be copied onto the payment entity. Verified in two steps — A-6A (notes survive on the order) and A-6B (payment exposes a usable `order_id`).
**Downside:** two API calls to correlate. Acceptable.

## D-006 — LLM proposes, deterministic code decides

**Date:** 2026-08-26 · *wording corrected*
**Reason:** money must not move on a model decision.

**Correction:** an earlier draft claimed this is "answered by the cart-verification demo." That was wrong. Cart verification demonstrates where AI is **deliberately not used**. It is not evidence of meaningful AI use. The two claims are separate.

**Where AI genuinely contributes:**
- converting unclear natural language into a structured shopping intent
- deciding which product descriptions and attributes actually match a request
- comparing ambiguous candidate products
- wording clarification questions *(code decides **whether** to ask; the model only writes it)*
- explaining unresolved exceptions in human terms

**What code owns, always:** quantities, prices, spending limits, confirmation, payment verification, idempotency, and every state change.

**Downside:** invites "is this really AI?" — answered by the list above plus the ablation, not by the cart verifier.

## D-007 — Browser checkout and Browser MCP are separate concerns

**Date:** 2026-08-26 · *corrected*
**Context:** Razorpay Checkout requires interactive browser participation (A-1A). Browser MCP is **one way** to automate a browser and must not be the only way to complete the demo. An earlier draft wrongly treated "browser required" as "Browser MCP required," which would have made MCP availability decide whether the repository works.
**Chosen:** build a normal Razorpay Checkout page as the reliable payment route. Browser MCP is an **optional** `CommerceAdapter` for the visible shopping demo.
**Fallback:** the user completes Razorpay test Checkout manually.
**Consequences:** repository stays reproducible without MCP; MCP can fail without blocking the payment demonstration; the demo still shows agent-driven shopping when MCP works.
**Evidence:** [S2S integration](https://razorpay.com/docs/payments/payment-gateway/s2s-integration/) · [Capture a payment](https://razorpay.com/docs/api/payments/capture/) · [Smart Collect test payments](https://razorpay.com/docs/payments/smart-collect/test-payments/)

## D-008 — Auto-capture enabled in Dashboard

**Reason:** authorized payments become captured with no separate API call — one less step on the critical path, one less failure mode in the demo.
**Downside:** less explicit control. Capture logic is not what is being demonstrated.
**Status:** ⏳ to be enabled by the user at CP-0.

## D-009 — Python version

**Date:** 2026-08-26 · **Decided: Python 3.14.4**
**Evidence:** `uv venv --python 3.14` succeeded; all of fastapi, sqlmodel, pydantic, pytest, hypothesis, razorpay, anthropic, httpx, python-dotenv, uvicorn installed and import cleanly.
**Fallback if needed:** 3.13. Not needed.

## D-010 — Track 04 fallback: scope and required metrics

**Date:** 2026-08-26
**Scope, stated exactly:**
> "Reconcile agent purchase intents, Razorpay orders/payments, and merchant orders; safely repair only uniquely proven state mismatches."

**Three sources must be visible in the report:**
```
Purchase intent  ↔  Razorpay order/payment  ↔  Merchant order
```

**CP-7 must report all of:** total records · correctly matched records · match rate · **false-match rate** · unresolved records · safe repairs · unsafe repairs · duplicate business effects · processing time · exception categories.

**Reason:** match rate alone is insufficient. A system can reach a high match rate by guessing dangerously. **False-match rate is reported separately, never folded in** — it is the number that shows whether the guesses were safe, and the first thing a payments judge will ask for.
**Downside:** more instrumentation at CP-7. Non-negotiable.

## D-011 — Model ID lives in `.env`, never hardcoded

**Date:** 2026-08-26
**Context:** `claude-opus-5` is the current Opus model ID, but availability depends on the account. Hardcoding it makes the repo fail for anyone cloning it, including a judge.
**Chosen:** `ANTHROPIC_MODEL` in `.env`; `.env.example` ships names only. The application must also run through the deterministic stub when no model is available (A-8).
**Verify:** step 9 reads `os.environ["ANTHROPIC_MODEL"]` and fails loudly if unset.

## D-012 — Payment completion is server-verified only

**Date:** 2026-08-26
**Context:** the checkout page's success handler proves only that the **browser** saw a success message. For a project whose pitch is safe money actions, trusting it would be an obvious hole — and the first thing a judge probes.

**Frozen requirements:**
- Never trust the browser's "payment successful" message.
- Verify `razorpay_signature` server-side with a **constant-time** comparison.
- Independently fetch the payment from Razorpay.
- Compare `status == captured`, amount, currency, and `order_id`.
- Only verified captured payments may update order history.
- The payment-verification endpoint must be **idempotent**.

**Interface:** `verify_payment(order_id, payment_id, signature) -> VerifiedPayment | Rejection`
**No other code path may mark a purchase complete.**
**Downside:** one extra API call per payment. Trivial.

## D-013 — `.claude/settings.local.json` is gitignored

**Date:** 2026-08-26
**Reason:** machine-local tooling settings; may contain permission grants specific to this machine. Not part of the deliverable.
