# OrderGuard

**Find → Verify → Pay → Prove**

OrderGuard is a deterministic financial-authorization boundary around a
probabilistic agent. Claude may search, read connector data, and recommend a
candidate. It never receives a payment-execution tool. After explicit user
selection, OrderGuard re-reads authoritative merchant state, evaluates the
gates, issues a signed single-use Authorization, and only then permits the
server-side Razorpay executor to create a test-mode payment.

## Current verified state

- `uv run pytest -q`: **629 tests pass** on 2026-08-31 (610-test baseline).
- `make eval`: passes and regenerates `results/latest.json`.
- `make feature-matrix`: 39 code-derived entries; none currently claims a
  fresh live verification.
- Anthropic API and Claude subscription adapters are offline-tested against
  one semantic result contract.
- Current live subscription attempt: **blocked** — configured
  `CLAUDE_CODE_OAUTH_TOKEN` returned HTTP 401.
- Current Swiggy health: **blocked** — Food, Instamart, and Dineout report
  disconnected and require user OAuth approval.
- GitHub hosted MCP: implemented and offline-tested; live proof needs a
  user-provided read-only PAT.

These blockers mean the repository is not yet claiming final Buildathon
acceptance. See [Implementation audit](docs/IMPLEMENTATION_AUDIT.md).

## Run locally

```bash
uv run pytest -q
make eval
make feature-matrix
make app
```

Open `http://127.0.0.1:8000/app`. The six product surfaces are BUY/MISSION,
CONNECTORS, ATTACK LAB, EVIDENCE, SYSTEM/FEATURES, and EVAL/JUDGE.

Runtime credentials stay server-side. Copy `.env.example` to `.env`, use
Razorpay **test-mode** keys only, and generate connector encryption material
as documented in that file. BYOK Anthropic keys live only in process memory
and can be forgotten explicitly.

## Security boundary

```text
Claude → eligible R0 connector reads → candidates / recommendation
user selection → fresh merchant cart re-read → deterministic gates
→ signed single-use Authorization → server-side Razorpay executor → evidence
```

R1/R2 tools remain disabled during the current read-only staging phase. R3
financial tools fail with `FinancialToolExposureError`; they are never
silently removed and never offered to either runtime.

Key references: [Architecture](ARCHITECTURE.md), [Security](SECURITY.md),
[API contracts](docs/API_CONTRACTS.md), [Connectors](docs/CONNECTORS.md),
[Feature matrix](docs/FEATURE_MATRIX.md), [Evaluation](EVALUATION.md), and
[What broke](WHAT_BROKE.md).
