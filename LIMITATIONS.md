# Limitations

What this project does **not** prove. Written before the results exist, so the
claims cannot drift to fit them.

---

## Scope

- **The prototype supports ONE merchant end to end.** The `CommerceAdapter`
  design shows how additional stores could be connected. That is not demonstrated.
- Single-tenant. No authentication, no high availability, no multi-user isolation.

## Data

- **All evaluation data is synthetic.** The injected failure frequencies are
  chosen by the generator and **do not represent production rates.**
- Memory is tested on a small synthetic purchase history, not on real usage.
- The LLM runs on a **free tier**. Free-tier Gemini inputs may be used to improve
  Google's products. All data here is synthetic and emails are hashed before
  reaching any prompt, but this is stated rather than hidden.
- Nothing here has been reproduced against live production traffic.

## Payments

- **Test mode only.** No live credentials, no real money, at any point.
- Payment authorization requires interactive checkout. The prototype uses a
  Razorpay Checkout page completed manually. Automating that step is optional
  and cosmetic — it does not change what is verified.
- **At-least-once processing with an exactly-once *business effect*.**
  This is **not** exactly-once network delivery, which is not achievable.

## Results

- **Zero unsafe repairs is a result on this dataset, under these assumptions.**
  It is not a production guarantee.
- Match rate is reported alongside **false-match rate**, because match rate alone
  can be inflated by dangerous guessing. Read both.
- Throughput figures are from a local SQLite database on one machine.

## Claims about Razorpay

- **No claim is made about Razorpay's internal systems.** The only claim is that
  no equivalent *public* product or plugin behaviour was found.
- The plugin gap was identified by **reading published source code**, not by
  reproducing a live production failure.
- Open GitHub issues are cited as current evidence. Closed issues are cited only
  as historical examples and are labelled as such.

## AI

- The model's contribution is bounded: converting language into a typed intent,
  matching product descriptions, comparing ambiguous candidates, wording
  clarifications, explaining exceptions.
- The model **never** decides quantities, prices, spending limits, confirmation,
  payment verification, idempotency, or any state change.
- Cart verification demonstrates where AI is *deliberately not used*. That is a
  separate claim from meaningful AI use, and the two are not conflated.
