# Current State

_Updated: 2026-08-26 · Checkpoint: **CP-0 (in progress)**_

## Workspace

```
/Users/adityakumarrai/razorpay
```

⚠️ **The path ends with a trailing space.** `~/razorpay` (no space) does not exist.
Always use `"$PWD"` in commands. Never the tilde form. See D-000.

## What works

- Git repository initialised. `.gitignore` is the **first commit** (`05f8331`).
- `.env.example` committed with key names only (`b2e0ce9`).
- `.env` created locally and **proven untracked** — `git add .env` is refused.
- Python **3.14.4** virtualenv created.
- All dependencies installed and importing: fastapi 0.141.1, sqlmodel 0.0.39,
  pydantic 2.13.4, pytest 9.1.1, hypothesis 6.165.10, razorpay 2.0.1,
  anthropic 1.0.0, httpx, python-dotenv, uvicorn.

## What is incomplete

- No application code. None should exist before CP-1.
- Razorpay test keys not yet supplied → steps 4–8 blocked.
- Anthropic key not yet supplied → step 9 blocked.
- Tracking docs: being created now.

## Known failures

- none

## Assumption status

| ID | Status |
|---|---|
| A-0 workspace path | ✅ **VERIFIED** — trailing space confirmed |
| A-1A captured payment needs interactive checkout | ✅ **ANSWERED: yes** (docs) |
| A-4 deps on Python 3.14 | ✅ **VERIFIED** — all installed, all import |
| A-1B manual checkout works | ⏳ needs Razorpay keys |
| A-2 test keys authenticate | ⏳ needs Razorpay keys |
| A-3 settlements in test mode | ⏳ needs Razorpay keys |
| A-5 Browser MCP reachability | ⏳ optional, not blocking |
| A-6A order notes survive | ⏳ needs Razorpay keys |
| A-6B payment exposes order_id | ⏳ needs Razorpay keys |
| A-7 strict Pydantic validation | ⏳ needs Anthropic key |
| A-8 offline testability | ⏳ requirement only; *verified* at CP-1 |

## Blocking questions

1. **Razorpay test keys** — needed for steps 4, 5, 6, 6b, 7, 8.
   Dashboard → Test Mode → Account & Settings → API Keys → Generate Test Key.
2. **Auto-capture enabled?** Dashboard → Account & Settings → Payment Capture → automatic (D-008).
3. **Anthropic API key** — needed for step 9.

## Exact next command

Once keys are in `.env`:

```bash
cd "$PWD" && set -a && . ./.env && set +a && \
curl -s -u "$RZP_KEY_ID:$RZP_KEY_SECRET" "https://api.razorpay.com/v1/payments?count=1" | head -c 400
```

## Track 01 status

**NOT STARTED.** Needs: demo merchant, checkout page, intent compiler,
cart verification, mandate gates, confirmation, payment verification, integrity core.

## Track 04 fallback status

**NOT STARTED.** Needs: generator, scorer, ≥50 journeys, exception report.
Becomes submittable at CP-7 — **and only once CP-7 reports the full metric set
in D-010, including false-match rate reported separately.**
