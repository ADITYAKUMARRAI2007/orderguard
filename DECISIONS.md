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

## D-014 — Browser MCP verified for the shopping adapter (A-5)

**Date:** 2026-08-26 · **Status: VERIFIED**
**Tested against** a local page on `localhost:8001`, with no Razorpay involvement.

| Capability | Result |
|---|---|
| Reach localhost | ✅ HTTP 200, page title and text read |
| Locate interactive elements | ✅ `button "Click me" [ref_1]` |
| Click and mutate the DOM | ✅ `MARKER_VALUE_7F3A9B` → `CLICKED_OK` |
| **Extract structured cart data** | ✅ `[{"sku":"milk_1l","qty":2},{"sku":"banana","qty":6}]` |

**Why the last row matters:** the browser adapter can implement `read_cart()`
returning **typed data**, not scraped prose. So `cart_verifier` compares
intent-vs-observed identically whether the cart was built through the demo-store
API or through the browser. The shampoo ×20 catch works on both paths.

**Does NOT change D-007.** Browser MCP remains an *optional* `CommerceAdapter`
for the shopping demo. The reliable payment route is still a normal Razorpay
Checkout page completed manually. This only means that when MCP is available,
the visual demo is genuinely available too.

## D-015 — Free LLM provider: Gemini Flash, provider-neutral config

**Date:** 2026-08-26 · *supersedes D-011's Anthropic assumption*

**Context:** the project should not require a paid API key. It must be runnable
by anyone cloning it, including a judge, at zero cost.

**Alternatives considered (Aug 2026 free tiers, no credit card):**

| Provider | Free limits | Structured output |
|---|---|---|
| **Gemini Flash** | ~10–15 RPM, ~1,500/day | ✅ **native `responseSchema`** |
| Groq | 30 RPM, 1k–14.4k/day | partial — true structured outputs only on newer models; else `json_object` |
| OpenRouter | 50 req/day | varies by model |
| Cloudflare Workers AI | 10k Neurons/day | limited |

**Chosen: Gemini Flash**, because native `responseSchema` maps directly onto the
strict-Pydantic contract already frozen in `docs/API_CONTRACTS.md`. Constrained
decoding means fewer validation rejections, which means fewer spurious
clarification questions in the demo.

**Config is provider-neutral:** `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL`.
Swapping to Groq is a config change plus one adapter, not a rewrite.

**Why a weaker free model is SAFE here (not a compromise):**
malformed output → schema rejection → clarification. Low confidence → escalation.
**It can never produce a payment.** The worst case is a chattier agent that asks
more questions, not a wrong purchase. This is a property of the gate design, and
it is worth stating in the panel.

**Caveats, stated openly:**
- Free-tier Gemini inputs may be used to improve Google's products. Acceptable
  here — all data is synthetic, no real customers, and emails are hashed before
  reaching any prompt. Must appear in `LIMITATIONS.md`.
- Free model catalogues change without notice; one provider silently deleted free
  models in May 2026 and broke live code. Absorbed by design: `StubProvider`
  means the test suite never touches the network, and the model is a config value.

**Evidence:** google-genai 2.20.0 installed and imports on Python 3.14.4.

## D-016 — LLM provider: Groq + `openai/gpt-oss-120b`

**Date:** 2026-08-26 · *supersedes D-015's Gemini choice*

**Context:** the Gemini free key proved unusable — every current model 403
"project denied access", every legacy model 404 "no longer available to new
users" (F-004). Not worth diagnosing on a 10-day clock.

**Chosen:** Groq, model `openai/gpt-oss-120b`.

**Evidence — tested, not assumed:**
- `GET /openai/v1/models` → 14 models available to this key
- `moonshotai/kimi-k2-instruct-0905` (my first pick) → **404, no access**
- Strict `json_schema` + `strict:true` verified working on three models:
  `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `openai/gpt-oss-20b`
- A-7a: valid structured output, correctly converting 500 rupees → 50000 paise
- A-7b: all five malformed outputs rejected by strict Pydantic

**Why gpt-oss-120b:** largest available on this key, 131K context, and it handled
the paise conversion without prompting tricks.

**Free tier:** 30 RPM, no card. The intent compiler runs a handful of times per
demo — far inside the limit.

**Cost of the swap:** two lines in `.env`. D-015's provider-neutral config paid
for itself twice in one day.

**Standing rule from this:** never trust a model list as entitlement. Both Gemini
and Groq advertised models the key could not call. Always probe with a real
request before writing code against a model.

## D-017 — HTTP client: `httpx` only

**Date:** 2026-08-26
**Context:** F-005. `urllib.request` received 403 from Groq where `curl` and
`httpx` received 200 — a User-Agent block at the edge.
**Decision:** all HTTP in this project uses `httpx`. No `urllib.request`.
**Also:** when diagnosing an API, use raw `curl` before any SDK. F-004 showed the
google-genai SDK hiding a clean 404 behind a 2-minute retry hang.

## D-018 — Vulcan: position against it, never claim integration

**Date:** 2026-08-26 · **Do at CP-8 (README), not before**

**Context:** Razorpay launched **Vulcan** on 18 Aug 2026 — a payments foundation
model covering routing, fraud, disputes and checkout personalisation.
**Verified: there is no public Vulcan API.** No endpoint, no SDK, no docs. It is
internal infrastructure. Code samples online showing `razorpay_vulcan_sdk` are
explicitly labelled hypothetical by their authors.

**Decision — two things, both cheap:**

1. **README framing (~30 min):**
   > *Vulcan predicts. OrderGuard verifies.*

   Vulcan answers *"will this payment succeed? is this fraud? which route?"*
   OrderGuard answers *"did the purchase reach the shop, exactly once?"*
   **A prediction can be entirely correct and the order still be lost**, because
   the loss happens *after* the prediction — in the gap between payment success
   and the shop recording it. This is the layer beneath Vulcan, not a competitor
   to it.

2. **An interface seam (~1 hr, at CP-3):** the ambiguity-adjudication step accepts
   an optional `external_risk_score`. Filled by our own simple scorer. Documented:
   *"if Razorpay exposed a Vulcan score, it plugs in here — and note it still
   cannot override a safety gate."*

**Hard rules:**
- **Never claim we integrated with Vulcan.** We cannot. A judge who built it spots
  the lie instantly and everything else becomes suspect.
- **Never repeat Vulcan's performance numbers as fact.** The 8–10% success-rate lift
  and 5x fraud-detection figures are self-reported beta results with no published
  methodology, baseline or sample period — journalists flagged this at launch.
  Quote them as *Razorpay's claims*, never as findings.

**Why it is worth doing:** knowing Vulcan exists is rare; knowing *what it does not
cover* shows we read their architecture rather than their press release.

**Why at CP-8 and not now:** designing around something we cannot test invites
overclaiming. At CP-8 we describe what we actually built.

## D-019 — Security: named attacks with tests, never "bulletproof"

**Date:** 2026-08-26

**Context:** the user asked for the system to be "full proof" against injection and
cyber threats. **No system is.** Claiming it would break our own no-false-confidence
rule, and a payments panel would treat the claim as naivety.

**Decision:** replace the unprovable claim with a provable one — a **named list of
attacks, each with a test**. "Here are 9 attacks and the tests that block them" is
stronger in a panel than "it is secure."

**In scope — attacks we test and block:**

| # | Attack | Defence | Test |
|---|---|---|---|
| 1 | Prompt injection via product name/description | gates are code over typed values; no text reaches them | `test_prompt_injection.py` |
| 2 | Forged payment success from the browser | server-side HMAC + independent fetch (D-012) | `test_payment_verification.py` |
| 3 | Replay — same webhook fired repeatedly | DB UNIQUE idempotency key claimed before write | `test_idempotency.py` |
| 4 | Cart tampered after user confirmation | `confirmed_cart_hash` frozen at confirmation (D-004) | `test_confirmation_gate.py` |
| 5 | XSS via product title in the demo shop | escape on render; no `innerHTML` with untrusted text | `test_xss.py` |
| 6 | SQL injection | SQLModel parameterised queries only; no string-built SQL | `test_sql_injection.py` |
| 7 | Secret leakage into the repo | `.gitignore` first commit; pre-commit grep; history audit | manual, 5-part audit |
| 8 | AI inventing fields or amounts | strict Pydantic `extra="forbid"`; schema has no amount field | `test_models.py` |
| 9 | Over-cap spend via persuasion | `WITHIN_CAP` gate is arithmetic, unreachable by text | `test_mandate_cap.py` |

**Explicitly out of scope — stated openly in LIMITATIONS.md:**
a compromised developer machine · anyone holding the `.env` file · denial of
service · Razorpay itself returning wrong data · supply-chain attacks on
dependencies · physical access.

**Consequence:** `LIMITATIONS.md` and `SECURITY.md` must both carry the sentence
*"this is not a security audit."* We demonstrate specific defences, not general
safety.

## D-020 — Shopify Storefront MCP is the real commerce integration

**Date:** 2026-08-27 · **Verified by direct test, not by search**

**Context:** the user asked for commerce MCP servers that are actually usable.
Swiggy's is official but invite-only with no sandbox (F-009). Zomato's is
unverified. Unofficial Blinkit/Zepto servers break ToS and place real orders.

**Finding:** **every Shopify store exposes an official MCP server at
`https://<store>/api/mcp`** — no API key, no approval, no app install.

Verified live against `allbirds.com` and `gymshark.com`, both HTTP 200.

**Tools (5):** `search_catalog` · `get_product_details` · `update_cart` ·
`get_cart` · `search_shop_policies_and_faqs`

**Full loop proven end to end against allbirds.com:**
1. searched the real catalogue — 10 real products with real prices
2. `update_cart` added quantity 2 to a **real cart**
3. `get_cart` read it back independently — a real cart id and checkout URL
4. compared intent (2) against observed (2) — MATCH

That is the entire OrderGuard thesis, running against a production store.

**Two incidental findings:**

1. **Shopify returns money as integer minor units** — `{"amount": 7500,
   "currency": "USD"}`. Same decision as our integer paise (D-002).

2. **`update_cart` returns an `instructions` field containing natural-language
   directions aimed at the AI** — *"Assist them in navigating to checkout by
   providing a markdown link to the checkout URL."* **The merchant's server is
   sending instructions to the agent, in production, on millions of stores.**
   A hostile merchant could put anything there. This makes our injection threat
   model concrete rather than hypothetical, and our gates are immune because
   they compare numbers and ids, never text.

**Decision:**
- `ShopifyMCPAdapter` becomes a real `CommerceAdapter` implementation.
- FreshCart stays as the offline/fallback adapter so tests never need the network.
- **Hard rule: we never complete a checkout on a third-party store.** We build a
  cart, read it back, verify, and stop at the checkout URL. Nothing ordered,
  nothing paid.
- Razorpay test mode remains the payment proof. This also avoids implying any
  Swiggy/Shopify–Razorpay commercial relationship that does not exist.

**Downside:** requires network, and a third-party store could change or rate-limit.
Mitigated by the FreshCart adapter and by never depending on it in tests.

**Evidence:** `curl -X POST https://allbirds.com/api/mcp -d
'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` → 200 with the tool list.
