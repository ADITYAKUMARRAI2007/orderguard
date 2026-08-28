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
| Gate evaluation latency, p50 | 0.059 ms |
| Gate evaluation latency, p95 | 0.221 ms |

Latency here is the deterministic decision layer only — comparing a typed cart against a typed intent and running twelve gates. It excludes the network calls to a merchant or to Razorpay, which this benchmark does not make; those are measured live in `make demo`.

## By attack category

| Category | Detected | Total | Rate |
|---|---:|---:|---:|
| correct | 15 | 15 | 100% |
| wrong_quantity | 5 | 5 | 100% |
| price_changed | 4 | 4 | 100% |
| wrong_variant | 4 | 4 | 100% |
| extra_item | 3 | 3 | 100% |
| missing_item | 3 | 3 | 100% |
| wrong_merchant | 3 | 3 | 100% |
| currency_mismatch | 3 | 3 | 100% |
| over_cap | 3 | 3 | 100% |
| cart_changed_after_confirm | 3 | 3 | 100% |
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
