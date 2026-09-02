# Implementation audit

Updated 2026-09-02 from source and executable tests, not README claims.
Regenerate the numbers below with `make test-report && make feature-matrix`
— they are read from `results/test_report.json` and
`results/feature_matrix.json`, never typed by hand.

## Baseline and checkpoint

- Current: **732 tests pass** (`results/test_report.json`).
- `make eval`: passes. `make feature-matrix`: **40 entries generated**
  (`results/feature_matrix.json`).
- Since the 2026-08-31 checkpoint this table previously described: added the
  Secret Executor (`executor.py`) and Execution Capability v1
  (`capability.py`) boundary, real post-payment gates (`G_NO_REFUND`,
  `G_SINGLE_CANDIDATE`, `G_NOT_EXPIRED` previously had no evaluation
  function at all), mechanical architecture-boundary tests
  (`tests/test_architecture_boundaries.py`), and replaced the
  server-rendered `web/` app with a React frontend under `frontend/` — the
  old `/app` route no longer exists.

## Source architecture and proven surfaces

| Area | Implementing source | Executable evidence | Current status |
|---|---|---|---|
| FastAPI backend (session/commerce endpoints) | `src/orderguard/app.py` | `test_app.py`, `test_payment_flow.py` | VERIFIED_TESTED |
| React frontend (Mission, Shop, Connectors, Attack Lab, Evidence, Features, Eval) | `frontend/src/` | `test_app.py` (XSS/session endpoints), manual browser verification during development | IMPLEMENTED; no automated browser/E2E suite yet |
| Secret Executor (sole Razorpay-credential holder) | `executor.py` | `test_capability.py`, `test_architecture_boundaries.py` | VERIFIED_TESTED; live-verified against real Razorpay test-mode orders |
| Execution Capability v1 (single-use, atomic, operation-bound) | `capability.py` | `test_capability.py` (incl. 50-way real concurrent consumption + mutation-test comparison) | VERIFIED_TESTED |
| Architecture boundary enforcement (agent cannot import payment code; single construction/issue/consume site) | `tests/test_architecture_boundaries.py` | itself — a CI-runnable test, not only documented | VERIFIED_TESTED; not yet wired into a GitHub Actions workflow (see gaps below) |
| OrderGuard MCP server | `mcp_server.py` | `test_mcp_server.py` | VERIFIED_TESTED |
| Shopify/FreshCart adapters and aggregation | `commerce/` | `test_shopify_mcp.py`, `test_freshcart.py`, `test_orchestrator.py` | VERIFIED_TESTED; historical Shopify live evidence only |
| Connector registry/eligibility | `agent/connector_registry.py`, `eligibility.py` | registry/eligibility/orchestrator tests | VERIFIED_TESTED |
| Dual runtime and actual result capture | `agent/runtime/` | `test_runtime_adapters.py`, `test_subscription_auth.py` | API offline-tested; subscription live attempt blocked by 401 |
| Strict normalizers | `agent/normalizer.py` | `test_normalizer.py` | VERIFIED_TESTED; Swiggy live fixture still blocked by OAuth |
| Decision Council | `decision_council.py` | `test_decision_council.py` | VERIFIED_TESTED |
| Gates and fresh cart reread | `checkout_guard.py`, payment path in `app.py` | checkout/payment tests, including merchant-side 1→5 mutation | VERIFIED_TESTED |
| Signed authorization/consumption | `authorization.py` | `test_authorization.py` | VERIFIED_TESTED |
| Ledger/Razorpay/reconciliation | `ledger.py`, `razorpay_client.py`, `webhooks.py` | ledger/payment/webhook tests | VERIFIED_TESTED; live test-mode run not repeated this checkpoint |
| Audit chain | `audit.py` | `test_audit.py`; `/api/audit/verify` | VERIFIED_TESTED |
| Connector accounts/BYOK | `agent/connector_accounts.py`, `runtime_settings.py` | connector-account/BYOK tests | VERIFIED_TESTED; owner-scoped local mode |
| Swiggy OAuth | `agent/swiggy_oauth.py` | PKCE/state/offline HTTP tests | IMPLEMENTED_UNVERIFIED; user OAuth required |
| GitHub hosted MCP | registry/runtime/normalizer | offline adapter/normalizer tests | IMPLEMENTED_UNVERIFIED; user PAT required |
| Custom MCP/SSRF | `custom_connectors.py`, `ssrf_guard.py` | custom/SSRF tests | VERIFIED_TESTED for discovery/control; general result normalization remains unsupported |
| Missions/action lifecycle | `missions.py`, `lifecycle.py` | mission/lifecycle tests | R0 mission reads tested; R1/R2 execution intentionally disabled |
| Attack Lab/baselines/eval | `benchmark.py`, `agent/attack_lab.py`, `scripts/eval.py` | attack/baseline/eval tests | VERIFIED_TESTED |
| Seven-screen React UI (superseded the old six-screen `web/` app, now deleted) | `frontend/src/pages/` | API/XSS tests; manual browser verification during development | IMPLEMENTED; no automated browser/E2E suite yet |

## Security-sensitive configuration

`.env.example` names Razorpay test keys, webhook secret, LLM/search keys,
`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, and
`CONNECTOR_TOKEN_KEY`. Values are never part of audit payloads or API
responses. The checked-in `.env` is ignored and was inspected only for
variable names, never printed.

## Confirmed mismatches and remaining acceptance gaps

- Historical docs described broad/best-effort Swiggy parsing; code now uses a
  strict fixture and rejects unknown aliases.
- Historical feature status used “shipped”; generated status now distinguishes
  tested from unverified and makes no fresh live claims.
- `make eval` currently produces `latest.json`; the full requested routing,
  parity, visual, and judge artifact set is not yet implemented.
- No neutral or adversarial fresh-context final report exists yet.
- Current external blockers: refresh Claude subscription token, approve
  Swiggy OAuth, provide a read-only GitHub PAT, and (for API live proof) enter
  a valid Anthropic API key.
