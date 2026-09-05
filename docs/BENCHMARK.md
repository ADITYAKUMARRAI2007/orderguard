# Adversarial cart-integrity benchmark

Fifty purchase journeys through the real pre-payment guard — `cart_verifier.compare_cart`, `checkout_guard.evaluate_pre_payment_gates`, and `ledger`'s idempotency functions. Not a simulation of the guard: the same code the running app calls.

Not Track 04's D-010 metric set — that reconciles intent, Razorpay order and merchant order after the fact. This benchmarks Track 01's own pre-payment decision, on the same principle: false-match rate is reported separately and never folded into an average that hides it.

## Headline

| Metric | Value |
|---|---|
| Total journeys | 50 |
| Overall match rate | 100% |
| **False-match rate** (attack wrongly allowed) | **0%** |
| False-block rate (correct cart wrongly blocked) | 0% |
| Duplicate business effects | 0 |
| Gate evaluation latency, p50 | 0.061 ms |
| Gate evaluation latency, p95 | 0.287 ms |

Latency here is the deterministic decision layer only — comparing a typed cart against a typed intent and running thirteen gates. It excludes the network calls to a merchant or to Razorpay, which this benchmark does not make; those are measured live in `make demo`.

## By attack category

| Category | Detected | Total | Rate |
|---|---:|---:|---:|
| correct | 13 | 13 | 100% |
| wrong_quantity | 5 | 5 | 100% |
| price_changed | 4 | 4 | 100% |
| wrong_variant | 4 | 4 | 100% |
| extra_item | 3 | 3 | 100% |
| missing_item | 3 | 3 | 100% |
| wrong_merchant | 3 | 3 | 100% |
| currency_mismatch | 3 | 3 | 100% |
| over_cap | 3 | 3 | 100% |
| cart_changed_after_confirm | 3 | 3 | 100% |
| stale_authorization | 2 | 2 | 100% |
| duplicate_checkout | 2 | 2 | 100% |
| model_insists_ok | 2 | 2 | 100% |

**Zero false matches.** No corrupted cart in this run was allowed through.

## Graduated fault injection

The fixed fifty above proves each attack is caught at least once. This asks a harder question: does that hold as corruption becomes MORE common, not merely present? The corruption rate is randomised per journey and seeded, so this table is exactly reproducible.

| Corruption rate | Journeys | False-match rate | False-block rate |
|---:|---:|---:|---:|
| 0% | 25 | **0%** | 0% |
| 5% | 25 | **0%** | 0% |
| 10% | 25 | **0%** | 0% |
| 20% | 25 | **0%** | 0% |
| 40% | 25 | **0%** | 0% |
| 80% | 25 | **0%** | 0% |
| 100% | 25 | **0%** | 0% |

Worst false-match rate across every corruption level tested: **0%**.

# Baselines — is independent re-verification actually necessary?

Same fixed-fifty scenario set (D-031), three configurations. The question this answers empirically: is a human confirming what the agent SAYS it did enough, or does the confirmation need to be checked against what the merchant actually recorded?

| Configuration | Unsafe acceptance | Valid acceptance | Amount leaked |
|---|---:|---:|---:|
| **no_guard** — Agent proposes a cart; it executes. No check at all. | 100% (37/37) | 100% (13/13) | ₹20,983.73 of ₹20,983.73 exposed |
| **confirm_only** — Human confirms the agent's own claimed summary. No independent merchant re-read, no gates, no idempotency. | 100% (37/37) | 100% (13/13) | ₹20,983.73 of ₹20,983.73 exposed |
| **orderguard** — Human confirms -> independent merchant re-read -> 13 deterministic gates -> payment. | 0% (0/37) | 100% (13/13) | ₹0.00 of ₹20,983.73 exposed |

`no_guard` and `confirm_only` score identically on this set — not a coincidence, and not a rigged strawman. Every one of the fixed fifty's attack categories tampers with what the merchant actually has, not with what the agent believes it asked for. Confirming an unverified belief does not verify it.
`orderguard`'s independent re-read is what changes the answer: 0% unsafe acceptance against 100% for both weaker configurations.
In real money, over this same scenario set: `no_guard` and `confirm_only` would have let ₹20,983.73 through on carts that did not match what was approved. `orderguard` let through ₹0.00 of that same ₹20,983.73 of exposure.
