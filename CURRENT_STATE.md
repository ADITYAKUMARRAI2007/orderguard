# Current State
Updated: 2026-08-28 · Checkpoint: CP-4 complete (cart verification works live)

## Workspace
/Users/adityakumarrai/razorpay      <- NOTE: trailing space. Always use "$PWD".

## What works — verified, not asserted
- Money as integer paise. Float, bool, and sub-paise all rejected.
- 21 gates frozen by name; 12 before payment, 9 after.
- Demo shop (FreshCart) with server-owned prices, escaped output, and a live
  prompt-injection product in the real catalogue.
- **Live Shopify Storefront MCP adapter.** Real catalogues, real INR prices.
- **Multi-store parallel search** across 5 grocery stores; a store that fails
  is recorded and the others continue.
- **Explicit user choice.** The app never picks between products.
- **Independent cart read-back.** Separate round trip from the write.
- **All 12 pre-payment gates, live: 12/12 against a real Slurrp Farm cart.**
- Web client at /app: request -> plan -> real offers -> choose -> verify.
- 120 tests pass with NO API key.

## Verified live, 28 Aug
    request  "two packs of millet cereal, under 700 rupees"
    search   5 stores, 24 offers, 0 failures
    choose   Trial Pack Millet Oat Porridge, Rs 94.05, Slurrp Farm
    write    add_to_cart -> real Shopify cart
    read     get_cart -> 2 items, Rs 188.10   (independent call)
    confirm  matches, hash frozen
    gates    12/12 passed, allow=True

## What is incomplete
- Razorpay payment leg and the idempotency ledger (CP-6). NOT STARTED.
- Integrity core, 50 journeys, exception report (CP-7, Track 04). NOT STARTED.
- Memory, README, application answers (CP-8).
- Demo recording (CP-9).

## Known failures
F-001..F-009 as recorded.
F-010 twelve gates and none checked the price.
F-011 the new price gate blocked its own happy path.

## Deliberate limits
- No checkout is ever completed on a third-party store. The cart is built, read
  back, verified, and the checkout URL is handed to the user. Nothing follows it.
- Payment proof runs through Razorpay test mode on our own merchant. Shopify
  merchants collect their own money; we do not fake that relationship.
- Carts on real stores are anonymous. No account, no address, no personal data.

## Exact next command
    make app     # then http://127.0.0.1:8000/app

## Next to build
CP-6: Razorpay test-mode payment, server-side verification (D-012), and the
idempotency ledger (70 replays -> 1 business effect).

## Track 04 fallback status
NOT STARTED. Needs generator, scorer, 50 journeys, exception report with the
full D-010 metric set including false-match rate reported separately.
