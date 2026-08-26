# Current State

_Updated: 2026-08-26 · Checkpoint: **CP-0 COMPLETE** · Next: **CP-1**_

## Workspace

```
/Users/adityakumarrai/razorpay
```

⚠️ Trailing space. `~/razorpay` does not exist. Always `"$PWD"`. See D-000.

## What works

- Git repo; `.gitignore` is the **first commit**. `.env` proven unstageable.
- Python **3.14.4**; all deps install and import.
- **Razorpay test keys authenticate** (A-2).
- **Real order created**, `purchase_intent_id` survives in `notes` (A-6A).
- **Real payment captured and SERVER-VERIFIED** (A-1B / D-012):
  constant-time signature check, independent fetch,
  `status=captured`, `amount=29900`, `currency=INR`, `order_id` matched.
- **Correlation path proven end to end** (D-005):
  `payment.order_id -> order -> notes.purchase_intent_id -> intent_probe_1`
- **Auto-capture confirmed working** — payment arrived `captured`, not `authorized` (D-008).
- **Groq LLM verified** (A-7): strict `json_schema` output valid;
  all five malformed outputs rejected by Pydantic.
- **Browser MCP verified** (A-5): read, click, DOM mutation, structured extraction.
- Settlements in test mode: HTTP 200, empty collections (A-3).
- 20 gates frozen by name; 8 interfaces frozen.

## Assumption status — 11 of 11 closed

| ID | Status |
|---|---|
| A-0 workspace path | ✅ trailing space confirmed |
| A-1A interactive checkout required | ✅ yes, verified in docs |
| A-1B manual checkout + verification | ✅ **PASS** |
| A-2 keys authenticate | ✅ HTTP 200 |
| A-3 settlements in test mode | ✅ empty collections |
| A-4 deps on Python 3.14 | ✅ all import |
| A-5 Browser MCP | ✅ incl. structured extraction |
| A-6A order notes survive | ✅ |
| A-6B payment exposes order_id | ✅ |
| A-7 strict schema + rejection | ✅ Groq / gpt-oss-120b |
| A-8 offline testability | requirement set; **verified at CP-1** |

## Known failures

Six logged in `FAILURE_LOG.md`, all real:
F-001 shell state · F-002 `.env` overwrite · F-003 Gemini schema constraint ·
F-004 Gemini key denied + model list lied · F-005 urllib 403 vs curl 200 ·
F-006 international card rejected, UPI unavailable.

## Blocking questions

None.

## Exact next command

    cd "$PWD" && pytest tests/test_money.py -v

(CP-1 — money, enums, models, PurchaseIntent, LLM stub. Not yet written.)

## Track 01 status

Payment path **proven**. Still needed: demo merchant, intent compiler,
cart verification, mandate gates, confirmation, integrity core.

## Track 04 fallback status

Not started. Needs generator, scorer, 50+ journeys, exception report.
Submittable at CP-7 — **only once the full D-010 metric set is reported,
including false-match rate separately.**
