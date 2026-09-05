# Feature matrix

Generated 2026-09-05T17:10:34.865763+00:00 by `scripts/feature_matrix.py` — this file and `results/feature_matrix.json` are written from the same list, so they cannot drift. No entry was added to round out a count.

**40 features total.**

## Gates (3)

- **13 pre-payment gates** — `VERIFIED_TESTED`; live verified: **no** — Merchant permitted, intent valid, fields complete, cart unique, attributes/quantities/prices/currency match, items available, within cap, confirmation matches, authorization fresh, idempotency free. (`src/orderguard/checkout_guard.py, src/orderguard/enums.py::GateName`)
- **9 post-payment gates** — `VERIFIED_TESTED`; live verified: **no** — Payment captured, no refund, amount/currency match, single candidate, correlation, order repairable, not expired, no prior effect. (`src/orderguard/enums.py::GateName`)
- **TOCTOU-safe authorization freshness** — `VERIFIED_TESTED`; live verified: **no** — A stale signed Authorization is rejected by G_AUTHORIZATION_FRESH even if every other check passes. (`src/orderguard/checkout_guard.py`)

## Decision Council (1)

- **Decision Council (bounded multi-agent recommendation)** — `VERIFIED_TESTED`; live verified: **no** — Filter -> Fit -> Critic -> code veto. Advisory only; never selects an offer or writes a cart. Falls back to a deterministic top-ranked survivor if the LLM proposes an ID that was never offered to it. (`src/orderguard/decision_council.py`)

## Payment (4)

- **Signed Authorization (Ed25519)** — `VERIFIED_TESTED`; live verified: **no** — Frozen, signed artifact issued once a cart passes every pre-payment gate; independently re-verifiable from its own bytes. (`src/orderguard/authorization.py`)
- **Idempotency ledger** — `VERIFIED_TESTED`; live verified: **no** — UNIQUE-constraint-backed; one business effect however many times a call is retried. (`src/orderguard/ledger.py`)
- **PAYMENT_UNKNOWN + webhook reconciliation** — `VERIFIED_TESTED`; live verified: **no** — A lost payment response is marked UNKNOWN, never silently treated as failed or captured; resolved via receipt lookup or a real webhook delivery, HMAC-verified, deduplicated by x-razorpay-event-id. (`src/orderguard/webhooks.py, src/orderguard/ledger.py::LedgerStatus.UNKNOWN`)
- **Real Razorpay integration (test mode)** — `VERIFIED_TESTED`; live verified: **no** — Order creation, checkout, constant-time signature verification, independent fetch. (`src/orderguard/razorpay_client.py`)

## Audit (1)

- **Tamper-evident audit chain** — `VERIFIED_TESTED`; live verified: **no** — Hash-chained (sha256(prev_hash || canonical_json(payload))); verify_chain recomputes every hash from stored content rather than trusting it. Refusals recorded with the same weight as actions. (`src/orderguard/audit.py`)

## Connectors (5)

- **Connector evidence/capability directory** — `VERIFIED_TESTED`; live verified: **no** — Every known commerce surface, its evidence tier (direct/connector-verified, available-untested, restricted, unavailable) and capability (cart-mutable vs. discovery-only), independent of each other. (`src/orderguard/connectors.py`)
- **Connector backend-type classification** — `VERIFIED_TESTED`; live verified: **no** — Distinguishes a real independently-reachable remote MCP endpoint from one that only exists inside Claude's own consumer app directory -- the exact conflation an external review caught. (`src/orderguard/connectors.py::ConnectorBackendType`)
- **Merchant reach resolution** — `VERIFIED_TESTED`; live verified: **no** — Shoppable / blocked-by-policy / not-reachable / unknown, with the reason stated -- never silently defaults to a web search. (`src/orderguard/merchants.py`)
- **Real Shopify Storefront MCP integration** — `VERIFIED_TESTED`; live verified: **no** — No key needed; live-verified against real stores' /api/mcp endpoints. (`src/orderguard/commerce/shopify_mcp.py`)
- **Swiggy Instamart/Food live proof (via a Claude session)** — `VERIFIED_TESTED`; live verified: **no** — Real search -> record_intent -> check_cart round trip, both allow and block cases, landing in the real audit chain. (`docs/CONNECTORS.md`)

## Eval (6)

- **Fixed-fifty benchmark** — `VERIFIED_TESTED`; live verified: **no** — 50 reproducible scenarios across 13 attack categories, run against this repo's own production gate code. (`src/orderguard/benchmark.py`)
- **Hostile Attack Lab** — `VERIFIED_TESTED`; live verified: **no** — Prompt injection, salami-slicing, lost-payment-response, Decision Council hallucination -- kept separate from the fixed fifty so the cited count never drifts. (`src/orderguard/benchmark.py::run_attack_lab`)
- **Injection-rate curve** — `VERIFIED_TESTED`; live verified: **no** — False-match/false-block rate as the corruption rate varies from 0% to 100%, seeded and reproducible. (`src/orderguard/benchmark.py::run_injection_curve`)
- **Baseline comparison** — `VERIFIED_TESTED`; live verified: **no** — No-guard / confirm-only / OrderGuard, over the same scenario set -- answers whether independent re-verification is actually necessary. (`src/orderguard/benchmark.py::run_baselines`)
- **make eval artifact pipeline** — `VERIFIED_TESTED`; live verified: **no** — The only writer of results/latest.json and docs/BENCHMARK.md; exits non-zero if any false-match rate is nonzero. (`scripts/eval.py`)
- **Reason codes** — `VERIFIED_TESTED`; live verified: **no** — OG-XXX-NNN structured codes for every gate refusal. (`src/orderguard/reason_codes.py`)

## MCP (1)

- **OrderGuard as an MCP server** — `VERIFIED_TESTED`; live verified: **no** — record_intent, check_cart, verify_audit_trail, recommend_connector, list_verified_connectors -- usable by any external assistant holding any connector's cart. (`src/orderguard/mcp_server.py`)

## Agent orchestrator (12)

- **Dual agent runtime (API + subscription)** — `VERIFIED_TESTED`; live verified: **yes** — AnthropicApiRuntime (Messages API MCP Connector beta) and SubscriptionAgentRuntime (Claude Agent SDK) share one AgentTurnResult shape; verified directly against each runtime's current docs to differ in wire format on purpose, not by oversight. Subscription path live-verified 2026-08-31: a real mission through the browser UI resolved a real address, called the real Swiggy Instamart search_products tool, and returned 33 real offers. AnthropicApiRuntime remains offline-tested only -- no real ANTHROPIC_API_KEY has been exercised end-to-end. (`src/orderguard/agent/runtime/`)
- **R3 (financial) tool exclusion** — `VERIFIED_TESTED`; live verified: **no** — A payment-capable tool can never enter either runtime's tool list -- enforced once, at the one function both adapters call, not per-runtime. (`src/orderguard/agent/tools.py`)
- **Universal ConnectorResult model** — `VERIFIED_TESTED`; live verified: **no** — A Pydantic discriminated union (commerce/dev-task/calendar/email/task/file/unsupported); only commerce and dev-task are wired to a live connector -- the rest are real, typed, unfilled extension points, not fabricated pipelines. (`src/orderguard/agent/results.py`)
- **Connector eligibility engine** — `VERIFIED_TESTED`; live verified: **no** — Deterministic: category match, reachable backend type, not policy-restricted, account connected where required. The LLM only ever picks inside this pre-filtered set. (`src/orderguard/agent/eligibility.py`)
- **Universal action-approval lifecycle** — `VERIFIED_TESTED`; live verified: **no** — R0 auto-executes, R1 needs opt-in, R2 needs explicit approval, R3 never enters this lifecycle at all -- structurally routed to the existing payment path instead. (`src/orderguard/agent/lifecycle.py`)
- **Commerce Missions** — `VERIFIED_TESTED`; live verified: **no** — Multi-intent decomposition; every intent independently risk-governed; only a financial sub-transaction is ever OrderGuard-authorized, never a merged global authorization. (`src/orderguard/agent/missions.py`)
- **Encrypted connector credential store** — `VERIFIED_TESTED`; live verified: **no** — Fernet-encrypted bearer tokens, owner-scoped (LOCAL_SINGLE_USER today; not process-global by construction), shared by both runtimes. (`src/orderguard/agent/connector_accounts.py`)
- **Real Swiggy backend OAuth (Developer flow)** — `VERIFIED_TESTED`; live verified: **yes** — OAuth 2.1 + PKCE + RFC 7591 dynamic client registration, confirmed self-serve on localhost directly from Swiggy's own docs. Live-verified 2026-08-31: a real browser OAuth round trip completed against mcp.swiggy.com, the resulting token was encrypted and stored in ConnectorAccount, and the connector immediately showed CONNECTED with no further steps. (`src/orderguard/agent/swiggy_oauth.py`)
- **GitHub connector (required non-commerce proof)** — `IMPLEMENTED_UNVERIFIED`; live verified: **no** — Chosen because it needs only a personal access token, not an OAuth app -- the fastest real path to a second, genuinely different connector. (`src/orderguard/agent/connector_registry.py`)
- **SSRF-hardened custom connectors** — `VERIFIED_TESTED`; live verified: **no** — HTTPS-only, private/loopback/link-local IP rejection, cross-host redirect rejection, re-resolved on every call (not just at registration) to close the DNS-rebinding gap; discovered tools stored disabled until explicitly enabled with a non-R3 risk tier. (`src/orderguard/agent/custom_connectors.py, src/orderguard/agent/ssrf_guard.py`)
- **BYOK Anthropic API key** — `VERIFIED_TESTED`; live verified: **no** — In-memory only; never written to disk, logged, or echoed back beyond a masked confirmation. Distinct from the server-managed .env key and the subscription runtime. (`src/orderguard/agent/runtime_settings.py`)
- **Connector backend compatibility matrix** — `VERIFIED_TESTED`; live verified: **no** — Every connector classified as REMOTE_MCP / NATIVE_API_ADAPTER / CUSTOM_MCP / CLAUDE_DIRECTORY_ONLY / BROWSER_HANDOFF / UNSUPPORTED before it can be labeled reachable. (`src/orderguard/connectors.py::ConnectorBackendType`)

## UI (7)

- **Mission screen + pipeline trace** — `VERIFIED_TESTED`; live verified: **no** — Real-time 3D WebGL mission pipeline (intent -> route -> connector -> tool call -> result), live per-step transparency (the model's own text + call duration, not just a pass/fail), and cross-turn conversation continuity so a reply to the model's own question reaches the same open conversation. (`frontend/src/pages/Mission.tsx, frontend/src/components/PipelineScene.tsx, frontend/src/components/pipeline/PipelineCanvas.tsx`)
- **Shop screen** — `VERIFIED_TESTED`; live verified: **no** — The real FreshCart/Shopify direct-search purchase flow (search -> select -> confirm -> Razorpay checkout), driving the same app.py session endpoints the automated payment tests exercise. The only place in the product a real Razorpay test payment can be completed end to end. (`frontend/src/pages/Shop.tsx, frontend/src/lib/shop.ts`)
- **Connectors screen** — `VERIFIED_TESTED`; live verified: **no** — Registry cards with live per-connector connect state, runtime settings (including BYOK and a live runtime-mode switch), 'Connected via your Claude account' live detection (claude mcp list, with an explicit loading state for its multi-second health check), and an 'Add Custom MCP' form. (`frontend/src/pages/Connectors.tsx`)
- **Attack Lab screen** — `VERIFIED_TESTED`; live verified: **no** — Every number sourced from results/latest.json; no number typed by hand. (`frontend/src/pages/AttackLab.tsx`)
- **Evidence screen + purchase receipt** — `VERIFIED_TESTED`; live verified: **no** — Independent audit-chain re-verification, a session lookup, and a full Evidence Receipt (gates, signed authorization, Razorpay ledger state, audit chain -- every field re-verified live, not read from a cached flag). (`frontend/src/pages/Evidence.tsx, frontend/src/components/ReceiptCard.tsx, src/orderguard/app.py::session_receipt`)
- **Features screen** — `VERIFIED_TESTED`; live verified: **no** — Renders this exact feature matrix. (`frontend/src/pages/Features.tsx`)
- **Eval / Judge screen** — `VERIFIED_TESTED`; live verified: **no** — This repo's own benchmark next to a separate, fresh-context evaluator's report -- never merged into one number. (`frontend/src/pages/Eval.tsx`)

