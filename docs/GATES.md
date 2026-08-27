# Safety Gates — frozen contract

**Frozen 2026-08-26 at CP-0.** These names do not change without a `DECISIONS.md` entry.

**Amended 2026-08-27 (D-024):** `G_PRICES_MATCH` added, taking the pre-payment set to
twelve and the total to twenty-one. Building the live Shopify adapter showed the original
eleven could not catch a merchant quoting one price during search and charging another in
the cart, as long as the total stayed under the cap. A cap is a ceiling, not a price check.
The list is frozen so a count is never invented after the fact — not so a hole stays open.

Every gate is **deterministic code over typed values**. No gate reads free text.
No model output can move one. **All must pass for money to move** — a single failure
blocks the action and produces a human-readable reason.

---

## Pre-payment: the twelve mandate gates

| # | `GateName` | Checks | Failure reason shown to the user |
|---|---|---|---|
| 1 | `G_MERCHANT_PERMITTED` | merchant ∈ mandate's allowed list | "Merchant *X* is not in your approved list" |
| 2 | `G_INTENT_VALID` | intent parses; `status == READY_FOR_CHECKOUT` | "The request could not be understood as a purchase" |
| 3 | `G_FIELDS_COMPLETE` | `missing_fields` is empty | "Still missing: *quantity*" |
| 4 | `G_CART_UNIQUE` | exactly one cart identified for this intent | "*N* carts match this request — cannot choose safely" |
| 5 | `G_ATTRIBUTES_MATCH` | every `required_attributes` entry satisfied | "Requested *A4, black*; cart has *A5, blue*" |
| 6 | `G_QUANTITIES_MATCH` | observed qty == intent qty, **per line** | "Requested 6 bananas; cart contains 60" |
| 6b | `G_PRICES_MATCH` | observed unit price == **the price quoted when the user chose** | "Quoted ₹12 each; the cart charges ₹80" |
| 7 | `G_ITEMS_AVAILABLE` | every line in stock | "*Brown bread* is out of stock" |
| 8 | `G_CURRENCY_MATCH` | cart currency == intent currency | "Cart is in USD; request was INR" |
| 9 | `G_WITHIN_CAP` | `cart_total_paise <= maximum_total_paise` | "Cart is ₹640; your limit is ₹500" |
| 10 | `G_CONFIRMATION_MATCHES` | confirmation exists **and** matches `confirmed_cart_hash` | "The cart changed after you confirmed it" |
| 11 | `G_IDEMPOTENCY_FREE` | this idempotency key has no completed effect | "This purchase has already been completed" |

**Gates 6 and 9 are the demo.** Bananas ×60 fails gate 6. A ₹640 cart under a ₹500
cap fails gate 9. Both are caught by arithmetic, not by the model, and the model
cannot argue past either.

**Gate 10 is why `cart_hash` is frozen at confirmation (D-004).** If the hash were
recomputed per attempt, an edited cart would silently pass a stale confirmation.

---

## Post-payment: the integrity gates

Run by the reconciliation core when deciding whether to **repair** a payment/store mismatch.

| # | `GateName` | Checks |
|---|---|---|
| 12 | `G_PAYMENT_CAPTURED` | Razorpay says `status == captured` |
| 13 | `G_NO_REFUND` | `refunded_paise == 0` |
| 14 | `G_AMOUNT_MATCH` | `payment.amount_paise == order.total_paise` (exact, no tolerance) |
| 15 | `G_CURRENCY_MATCH_POST` | payment currency == order currency |
| 16 | `G_SINGLE_CANDIDATE` | exactly one store order matches |
| 17 | `G_CORRELATION` | `payment.order_id` → order → `notes.purchase_intent_id` resolves (D-005) |
| 18 | `G_ORDER_REPAIRABLE` | store order status is `pending` |
| 19 | `G_NOT_EXPIRED` | if time-sensitive, `now < fulfilment_deadline` |
| 20 | `G_NO_PRIOR_EFFECT` | ledger has no successful action for this key |

**Gate 14 has zero tolerance.** Tolerances on money are how reconciliation tools
corrupt books. A mismatch escalates; it never "rounds to close enough."

**Gate 16 is the refusal that matters.** Two plausible orders → refuse, escalate to
a human with both candidates ranked. Never guess.

---

## `GateResult` — frozen shape

```
GateResult:
    allow:   bool
    passed:  list[GateName]
    failed:  list[GateName]
    reasons: dict[GateName, str]     # human-readable, shown to the user
```

`allow` is `True` only when `failed` is empty. There is no partial pass,
no override flag, and no path for a model to set `allow`.

---

## Test contract

**One test per gate, pass and fail.** Twenty gates → forty tests minimum.

Plus these Hypothesis properties, which must hold over *arbitrary* generated input:

1. No input combination produces `allow=True` when `amount_paise` differs.
2. No input combination produces `allow=True` when more than one candidate survives.
3. No request exceeding `maximum_total_paise` ever reaches the payment call.
4. No sequence of retries produces more than one business effect.

---

## Why this document exists

Before it, the plan said "eleven gates" — a **count**, which is a presentation claim.
Naming them makes it an **implemented contract** with a test attached to each.
A judge can now check the claim against the code.
