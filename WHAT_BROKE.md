# What broke

The detailed chronological record is [FAILURE_LOG.md](FAILURE_LOG.md), 34
entries, written as failures happened rather than reconstructed afterward.
This page orders the same material by what it demonstrates about runtime
failure recovery, not by when it happened — the deepest findings first,
routine environmental noise last.

## The failures that mattered

| Failure | Cause | Discovery | Fix |
|---|---|---|---|
| A real deployment could reach every read endpoint but not the one that searches a store — Chrome reported it as a **CORS** error | Not CORS at all: an unhandled 500 (the demo store's own service, `demo_store/app.py`, had never been deployed — `FreshCartAdapter` was defaulting to `127.0.0.1:8002`, which does not exist on Render) reaches the browser with no CORS header, and Chrome's error message names the *symptom*, not the *cause* | Render's own log stream, read layer by layer, not the browser console (F-034) | Deployed the missing second service, wired `FRESHCART_URL`, and added server-side response-body logging to both LLM providers so the next credential/config mismatch shows Google's or Groq's own error text instead of a dead end |
| 50-way concurrent capability consumption didn't just fail the safety property — it crashed with `sqlite3.InterfaceError: bad parameter or other API misuse` | Python's `sqlite3` driver is not safe for two threads to call `execute()`/`commit()` on the *same connection object* at once, even with `check_same_thread=False` — a different failure than a race in the SQL `WHERE` clause itself | genuine OS threads (`asyncio.to_thread`) hitting the shared connection in the concurrency test, not a mocked one | a `threading.Lock` around the read-modify-read sequence in `consume_capability` — SQLite was always serializing the actual writes; the lock only stops the driver being handed two simultaneous calls it was never safe to accept (`capability.py`'s own docstring) |
| The expiry check could raise `TypeError: can't compare offset-naive and offset-aware datetimes` | SQLite (via SQLModel's default `DateTime` column) silently strips `tzinfo` on round-trip; a value read back from a `SELECT` is naive even when what was inserted was timezone-aware | the expiry test actually failing, not predicted ahead of time | every datetime this module stores or compares is UTC by convention, so `tzinfo` is dropped consistently on both sides rather than special-cased per read-back site |
| A capability minted for one operation could be consumed by *any* executor call that only checked amount/currency/merchant | `consume_capability` never checked what the capability's own `operation` field said | source audit, not a production incident | `CAPABILITY_WRONG_OPERATION`, checked by the caller and enforced inside the same atomic `WHERE` clause that already gates single-use consumption |
| Subscription runtime counted a tool as complete when its result never arrived | `ToolResultBlock` was ignored; requests and results were never correlated | source audit | correlate by tool-use ID; a missing result now fails closed instead of silently passing |
| A false block ("cart doesn't match") fired on every *correct* cart the moment the price gate shipped | the check trusted `unit_price_paise`, a field real Shopify carts leave `None` on purpose (a discount spread over units doesn't divide evenly) | first live run against a real store, nine of twelve gates failing on a cart that was entirely right (F-011) | compare line totals (quantity × approved unit price) against the observed line total — exact integer arithmetic, the field the model actually guarantees |
| Twelve gates and none of them checked the price | "within cap" was read as "the money is checked" — a ceiling permits every price beneath it, including one the user never saw | a test written to prove the gap, which passed against the *old* code (F-010) | `G_PRICES_MATCH`, exact equality against the price the user actually approved |

## Environmental / infrastructure noise

Real, but not evidence about the architecture — grouped rather than given a
row each:

- Offline evaluation could crash inside Homebrew `uv` before project code ran (macOS network init); fixed with `--offline --no-sync` on the `eval` / `feature-matrix` Make targets.
- A stale/revoked `CLAUDE_CODE_OAUTH_TOKEN` produced a real 401 on a live Subscription-runtime run, which also exposed an SDK async-generator cleanup race on the exception path (F-033) — fixed in the runtime; the token itself needs `claude setup-token` re-run periodically, same as any OAuth credential.
- Swiggy connectors show `AUTH_REQUIRED` on the current deployment until the backend OAuth flow is completed there — a "not yet connected on this host" state, not a code defect (`docs/CONNECTORS.md`).
- All six screens overflowed at 390px/768px under six-item desktop-sized nav; shared responsive rules fixed all six, verified by measured scroll widths, not just visual inspection.
