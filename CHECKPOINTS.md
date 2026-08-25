# Checkpoints

A checkpoint is complete only when **all** of these are true:
code runs · focused tests pass · regression tests pass · you understand the important code ·
diff reviewed · docs updated · failures recorded · no safety invariant weakened ·
no secret committed · next checkpoint's interface is clear.

⚠️ = safety-critical. Requires **Review A** (correctness) and **Review B** (adversarial)
by *different* agents. The implementation pass never counts as verification.
If separate agents are unavailable, run two separated passes and **label them non-independent**.

---

## CP-0 — Plan validated, environment proven · 26 Aug

**Objective:** prove every technical assumption before writing application code.

| Done | Item |
|---|---|
| ✅ | A-0 workspace path confirmed (trailing space) |
| ✅ | `.gitignore` is the **first commit** (`05f8331`) |
| ✅ | `.env` created and proven untracked (`git add .env` refused) |
| ✅ | `.env.example` committed with names only (`b2e0ce9`) |
| ✅ | A-4 dependencies install on Python 3.14.4 and import cleanly |
| ✅ | D-009 recorded: Python 3.14.4 |
| ✅ | D-003 recorded: SQLModel |
| ✅ | Tracking documents created |
| ⏳ | A-2 Razorpay test keys authenticate |
| ⏳ | D-008 auto-capture enabled in Dashboard |
| ⏳ | A-6A order notes survive create → fetch |
| ⏳ | A-1B manual checkout completed at least once |
| ⏳ | **D-012 payment verified SERVER-SIDE** — signature, independent fetch, field equality |
| ⏳ | A-6B payment exposes usable `order_id` |
| ⏳ | A-3 settlements behaviour in test mode recorded |
| ⏳ | A-7 strict Pydantic validation + invalid outputs rejected |
| ⏳ | D-011 `ANTHROPIC_MODEL` resolves from `.env` |
| ⏳ | A-8 offline testability **requirement established** (*verified* at CP-1) |
| ⏳ | A-5 Browser MCP status recorded separately (optional) |
| ⏳ | Eleven gates frozen by name |
| ⏳ | Track 04 metric set frozen (D-010) |
| ⏳ | You can explain the cart verifier's role **and** where AI genuinely contributes |

**Understanding check:** what does `.gitignore` **not** protect against?
**Go / No-go:** [ ]

---

## CP-1 — Money, enums, models, PurchaseIntent, LLM stub · 27 Aug

**Command:** `pytest tests/test_money.py -v && ANTHROPIC_API_KEY= pytest -v`
**Exit:** paise round-trip exact · float in a money field raises · enums frozen ·
**full suite passes with no API key** · `test_llm_stub.py` proves all five A-8 properties
(instantiates · compiles a known sentence · byte-identical on repeat ·
rejects unsupported input safely · **makes no network request**).
**Understanding check:** why integer paise and not `Decimal` everywhere?
**Go / No-go:** [ ]

## CP-2 — Demo merchant + checkout page + seeded generator · 28 Aug

*(generator moved here from CP-7 — C-2; checkout page here — C-1)*
**Command:** `curl localhost:8001/catalog && make reproduce SEED=1`
**Exit:** catalog serves · checkout page completes a manual test payment ·
same seed twice is byte-identical · runtime cannot read `data/truth/`
**Go / No-go:** [ ]

## CP-3 — Intent compiler + clarification · 29 Aug

**Command:** `pytest tests/test_intent_compiler.py -v`
**Exit:** missing quantity **and** missing budget each trigger a question ·
invalid model output produces a clarification, **never** a payment
**Go / No-go:** [ ]

## CP-4 — Cart reader + cart verifier · 30 Aug · **NEVER CUT**

**Command:** `pytest tests/test_quantity_verification.py -v`
**Exit:** bananas ×60 against an intent of ×6 blocks checkout · over-cap blocks
**Understanding check:** which component caught it, and could the model override it?
**Reviews:** A [ ] B [ ] Independent? [ ]
**Go / No-go:** [ ]

## CP-5 — Mandate gates + confirmation · 31 Aug · ⚠️

**Command:** `pytest tests/test_mandate_cap.py tests/test_confirmation_gate.py -v`
**Exit:** all eleven gates enforced, one test each · prompt-injection corpus moves nothing
**Reviews:** A [ ] B [ ] Independent? [ ]
**Go / No-go:** [ ]

## CP-6 — Payment leg + ledger + idempotency · 1 Sep · ⚠️

**Command:** `make idempotency && make pay-demo`
**Exit:** order created headless · checkout completed (manual path acceptable) ·
payment **verified server-side** (D-012) · 70 duplicate events → exactly 1 business effect ·
timeout-after-success → `ALREADY_RESOLVED`
**Understanding check:** exactly-once *delivery* vs exactly-once *business effect*?
Which single step of the payment path needs a browser, and why?
**Reviews:** A [ ] B [ ] Independent? [ ]
**Go / No-go:** [ ]

> 🚦 **HONEST CHECKPOINT — 1 SEPTEMBER.**
> If a verified captured payment is not working tonight, **cut memory (CP-8) now**
> and fall back to Track 04. Do not wait until the 4th.

## CP-7 — Integrity core + 50 journeys + exception report · 2 Sep · ⚠️

**Command:** `make demo && make score`
**Exit:** **Track 04 submittable** — but only once the full D-010 metric set is reported:
total · matched · match rate · **false-match rate (separate)** · unresolved ·
safe repairs · unsafe repairs · duplicate effects · processing time · exception categories.
Judge-chosen seed reproduces.
**Reviews:** A [ ] B [ ] Independent? [ ]
**Go / No-go:** [ ]

## CP-8 — Memory + README + application answers · 3 Sep

**Command:** `pytest tests/test_memory_*.py -v`
**Exit:** only completed orders trusted · current instruction overrides memory ·
memory cannot raise the cap · suggestions never auto-add · README written ·
application form answers drafted
**Go / No-go:** [ ]

## CP-9 — Demo recording · 4 Sep · **WORKING DEADLINE**

**Command:** `make demo-record`
**Exit:** 5-minute video recorded **in segments** so one browser failure cannot sink the take.
Browser MCP used for shopping if available; manual walkthrough otherwise. Either is acceptable.
**Go / No-go:** [ ]

## CP-10 — Submission only · 5 Sep

**No new features.** Five-part secret audit (see `SECURITY.md`). Rotate test keys after submitting.
**Go / No-go:** [ ]
