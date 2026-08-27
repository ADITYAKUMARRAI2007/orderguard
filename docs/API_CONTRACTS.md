# Frozen Interfaces

**Frozen 2026-08-26 at CP-0.** Nothing here changes after CP-1 without a `DECISIONS.md` entry.

These eight contracts are what let the pieces be built and reviewed independently.

---

## 1. Money — integer paise

**Rule: integer paise in storage and all logic. `Decimal` only at display and parsing boundaries. `float` never.**

```
to_paise(Decimal("24.99"))  -> 2499
to_display(2499)            -> Decimal("24.99")
to_paise(24.99)             -> raises TypeError     # float rejected at the door
```

**Why not `Decimal` throughout?** Because equality is the operation that matters here.
Gate 14 compares payment amount to order total with `==` and zero tolerance. Integers
make that comparison exact and un-arguable. `Decimal` would work but invites
"close enough" reasoning; `float` would silently break it.

---

## 2. `PurchaseIntent` — the typed contract

The output of the intent compiler, and the reference every later stage compares against.

```
PurchaseIntent:
    intent_id:                 str
    user_id:                   str
    merchant:                  str
    items:                     list[IntentItem]
    maximum_total_paise:       int
    currency:                  str
    delivery_deadline:         datetime | None
    requires_final_confirmation: bool
    missing_fields:            list[str]
    status:                    IntentStatus
    confirmed_cart_hash:       str | None     # set ONCE at confirmation (D-004)

IntentItem:
    requested_product:    str
    quantity:             int
    unit:                 str
    required_attributes:  dict[str, str]      # must match, else gate 5 fails
    preferred_attributes: dict[str, str]      # nice to have, never blocks
    allow_substitution:   SubstitutionPolicy
```

**The agent cannot silently change this object.** Any change requires re-confirmation.

---

## 3. `CommerceAdapter` — the swappability contract

```
CommerceAdapter (protocol):
    search(query, limit) -> list[Offer]
    add_to_cart(variant_id, quantity, cart_id?) -> ObservedCart
    read_cart(cart_id) -> ObservedCart
```

Implemented by the offline **FreshCart** API and `ShopifyMCPAdapter` for a
small allowlist of independently probed stores. A Shopify cart is read back
before it is trusted; OrderGuard stops before third-party checkout.

**This protocol is why Browser MCP is optional rather than load-bearing (D-007).**
Verified at CP-0: MCP can extract structured cart data
(`[{"sku":"milk_1l","qty":2}]`), so `read_cart()` returns typed data on both
paths and `cart_verifier` behaves identically. See D-014.

`ObservedCart` is what the store *actually contains* — never what the agent
*believes* it contains. That distinction is the entire point of the verifier.

### Cart expectation and comparison

The language model never picks a cart line by title. Once the user selects an
offer, the UI records `CartExpectation`: merchant, currency, maximum total and
the exact `variant_id → quantity` list. `compare_cart()` independently compares
that expectation with an `ObservedCart`. It rejects an unexpected variant,
quantity change, merchant/currency mismatch, or a total over the cap. Its result
is an input to the payment gate, never payment authorisation by itself.

---

## 4. `GateResult`

See `docs/GATES.md` for the twenty gate names and their meanings.

```
GateResult:
    allow:   bool                      # True only when failed is empty
    passed:  list[GateName]
    failed:  list[GateName]
    reasons: dict[GateName, str]
```

No override flag. No path for a model to set `allow`.

---

## 5. Idempotency key

```
merchant_id | purchase_intent_id | action_type | cart_hash
```

`cart_hash` is computed **once at user confirmation** and stored on the intent (D-004).
Retries reuse the stored value. Recomputing per attempt would produce a different key
and idempotency would silently fail.

Enforced by a **database UNIQUE constraint**, claimed **before** the store write.
Application-level checks race; the database does not.

---

## 6. `verify_payment` — no other path may complete a purchase

```
verify_payment(order_id, payment_id, signature) -> VerifiedPayment | Rejection
```

Steps, in order (D-012):
1. HMAC-SHA256 over `f"{order_id}|{payment_id}"` with the key secret
2. **Constant-time** comparison (`hmac.compare_digest`) against `signature`
3. **Independent fetch** of the payment from Razorpay
4. Equality on `status == "captured"`, `amount`, `currency`, `order_id`

Any failure returns `Rejection`. The endpoint is **idempotent**.

**The browser's success message is evidence of nothing.** It proves only that the
browser saw a success message.

---

## 7. Audit event

```
AuditEvent:
    seq:        int
    event_type: str
    payload:    dict
    prev_hash:  str | None
    entry_hash: str          # sha256(prev_hash || canonical_json(payload))
    created_at: datetime
```

Append-only. Any retrospective edit breaks every later hash.
**Refusals are recorded with the same weight as actions** — the exception list is
the deliverable, not an apology.

---

## 8. `LLMProvider` — and its stub

```
LLMProvider (protocol):
    complete(system, user, schema) -> validated object | ProviderError
```

Two implementations: **`AnthropicProvider`** (reads `ANTHROPIC_MODEL` from env,
never hardcoded — D-011) and **`StubProvider`** (deterministic, offline).

**Hard requirement (A-8):** the entire test suite passes with `ANTHROPIC_API_KEY` unset.
Verified at CP-1 by `tests/test_llm_stub.py`, which must prove all five:

1. the stub instantiates
2. it compiles a known sentence into a valid `PurchaseIntent`
3. repeated runs return **byte-identical** output
4. an unsupported case is rejected **safely** — clarification or error, never a payment
5. **no network request is made** (socket guard)

**Every model response is validated against a strict Pydantic schema with
`extra="forbid"` before use.** Invalid output, timeout, rate limit, or low confidence
produces a clarification or escalation — never an automatic financial action.

---

## Enums — frozen

`PaymentStatus` · `OrderStatus` · `IntentStatus` · `Action` · `Classification` ·
`SubstitutionPolicy` · `ClarificationReason` · `GateName`

Strings are not used for state anywhere. A typo in a string is a runtime surprise;
a typo in an enum member is an import error.
