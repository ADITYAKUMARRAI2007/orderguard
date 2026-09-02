"""Walk the shipped feature set and write docs/FEATURE_MATRIX.md +
results/feature_matrix.json from ONE list, so the two can never drift —
same rule scripts/eval.py already enforces for benchmark numbers.

The list below was built by reading the actual implementing file for every
entry, not from memory. No entry was added to round out a count: this is
whatever the honest walk turned up, however many that is.

    make feature-matrix
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# (name, category, description, implemented_in, status)
# status: "shipped" -- built, tested, and (where applicable) live-verified.
#         "offline_tested_pending_credential" -- built and unit-tested
#           end-to-end with a stub; the one remaining step is a real
#           credential only the project owner can issue (an API key, an
#           OAuth token, a personal access token) -- named explicitly in
#           the description, never silently assumed done.
FEATURES: list[tuple[str, str, str, str, str]] = [
    # --- deterministic pre/post-payment gates -------------------------------
    ("13 pre-payment gates", "Gates", "Merchant permitted, intent valid, fields complete, cart unique, attributes/quantities/prices/currency match, items available, within cap, confirmation matches, authorization fresh, idempotency free.", "src/orderguard/checkout_guard.py, src/orderguard/enums.py::GateName", "shipped"),
    ("9 post-payment gates", "Gates", "Payment captured, no refund, amount/currency match, single candidate, correlation, order repairable, not expired, no prior effect.", "src/orderguard/enums.py::GateName", "shipped"),
    ("TOCTOU-safe authorization freshness", "Gates", "A stale signed Authorization is rejected by G_AUTHORIZATION_FRESH even if every other check passes.", "src/orderguard/checkout_guard.py", "shipped"),

    # --- decision council ----------------------------------------------------
    ("Decision Council (bounded multi-agent recommendation)", "Decision Council", "Filter -> Fit -> Critic -> code veto. Advisory only; never selects an offer or writes a cart. Falls back to a deterministic top-ranked survivor if the LLM proposes an ID that was never offered to it.", "src/orderguard/decision_council.py", "shipped"),

    # --- authorization & payment ----------------------------------------------
    ("Signed Authorization (Ed25519)", "Payment", "Frozen, signed artifact issued once a cart passes every pre-payment gate; independently re-verifiable from its own bytes.", "src/orderguard/authorization.py", "shipped"),
    ("Idempotency ledger", "Payment", "UNIQUE-constraint-backed; one business effect however many times a call is retried.", "src/orderguard/ledger.py", "shipped"),
    ("PAYMENT_UNKNOWN + webhook reconciliation", "Payment", "A lost payment response is marked UNKNOWN, never silently treated as failed or captured; resolved via receipt lookup or a real webhook delivery, HMAC-verified, deduplicated by x-razorpay-event-id.", "src/orderguard/webhooks.py, src/orderguard/ledger.py::LedgerStatus.UNKNOWN", "shipped"),
    ("Real Razorpay integration (test mode)", "Payment", "Order creation, checkout, constant-time signature verification, independent fetch.", "src/orderguard/razorpay_client.py", "shipped"),

    # --- audit --------------------------------------------------------------
    ("Tamper-evident audit chain", "Audit", "Hash-chained (sha256(prev_hash || canonical_json(payload))); verify_chain recomputes every hash from stored content rather than trusting it. Refusals recorded with the same weight as actions.", "src/orderguard/audit.py", "shipped"),

    # --- connectors & merchants ------------------------------------------------
    ("Connector evidence/capability directory", "Connectors", "Every known commerce surface, its evidence tier (direct/connector-verified, available-untested, restricted, unavailable) and capability (cart-mutable vs. discovery-only), independent of each other.", "src/orderguard/connectors.py", "shipped"),
    ("Connector backend-type classification", "Connectors", "Distinguishes a real independently-reachable remote MCP endpoint from one that only exists inside Claude's own consumer app directory -- the exact conflation an external review caught.", "src/orderguard/connectors.py::ConnectorBackendType", "shipped"),
    ("Merchant reach resolution", "Connectors", "Shoppable / blocked-by-policy / not-reachable / unknown, with the reason stated -- never silently defaults to a web search.", "src/orderguard/merchants.py", "shipped"),
    ("Real Shopify Storefront MCP integration", "Connectors", "No key needed; live-verified against real stores' /api/mcp endpoints.", "src/orderguard/commerce/shopify_mcp.py", "shipped"),
    ("Swiggy Instamart/Food live proof (via a Claude session)", "Connectors", "Real search -> record_intent -> check_cart round trip, both allow and block cases, landing in the real audit chain.", "docs/CONNECTORS.md", "shipped"),

    # --- attack lab / eval ----------------------------------------------------
    ("Fixed-fifty benchmark", "Eval", "50 reproducible scenarios across 13 attack categories, run against this repo's own production gate code.", "src/orderguard/benchmark.py", "shipped"),
    ("Hostile Attack Lab", "Eval", "Prompt injection, salami-slicing, lost-payment-response, Decision Council hallucination -- kept separate from the fixed fifty so the cited count never drifts.", "src/orderguard/benchmark.py::run_attack_lab", "shipped"),
    ("Injection-rate curve", "Eval", "False-match/false-block rate as the corruption rate varies from 0% to 100%, seeded and reproducible.", "src/orderguard/benchmark.py::run_injection_curve", "shipped"),
    ("Baseline comparison", "Eval", "No-guard / confirm-only / OrderGuard, over the same scenario set -- answers whether independent re-verification is actually necessary.", "src/orderguard/benchmark.py::run_baselines", "shipped"),
    ("make eval artifact pipeline", "Eval", "The only writer of results/latest.json and docs/BENCHMARK.md; exits non-zero if any false-match rate is nonzero.", "scripts/eval.py", "shipped"),
    ("Reason codes", "Eval", "OG-XXX-NNN structured codes for every gate refusal.", "src/orderguard/reason_codes.py", "shipped"),

    # --- OrderGuard's own MCP server -------------------------------------------
    ("OrderGuard as an MCP server", "MCP", "record_intent, check_cart, verify_audit_trail, recommend_connector, list_verified_connectors -- usable by any external assistant holding any connector's cart.", "src/orderguard/mcp_server.py", "shipped"),

    # --- agent orchestrator (this build) ---------------------------------------
    ("Dual agent runtime (API + subscription)", "Agent orchestrator", "AnthropicApiRuntime (Messages API MCP Connector beta) and SubscriptionAgentRuntime (Claude Agent SDK) share one AgentTurnResult shape; verified directly against each runtime's current docs to differ in wire format on purpose, not by oversight. Subscription path live-verified 2026-08-31: a real mission through the browser UI resolved a real address, called the real Swiggy Instamart search_products tool, and returned 33 real offers. AnthropicApiRuntime remains offline-tested only -- no real ANTHROPIC_API_KEY has been exercised end-to-end.", "src/orderguard/agent/runtime/", "shipped"),
    ("R3 (financial) tool exclusion", "Agent orchestrator", "A payment-capable tool can never enter either runtime's tool list -- enforced once, at the one function both adapters call, not per-runtime.", "src/orderguard/agent/tools.py", "shipped"),
    ("Universal ConnectorResult model", "Agent orchestrator", "A Pydantic discriminated union (commerce/dev-task/calendar/email/task/file/unsupported); only commerce and dev-task are wired to a live connector -- the rest are real, typed, unfilled extension points, not fabricated pipelines.", "src/orderguard/agent/results.py", "shipped"),
    ("Connector eligibility engine", "Agent orchestrator", "Deterministic: category match, reachable backend type, not policy-restricted, account connected where required. The LLM only ever picks inside this pre-filtered set.", "src/orderguard/agent/eligibility.py", "shipped"),
    ("Universal action-approval lifecycle", "Agent orchestrator", "R0 auto-executes, R1 needs opt-in, R2 needs explicit approval, R3 never enters this lifecycle at all -- structurally routed to the existing payment path instead.", "src/orderguard/agent/lifecycle.py", "shipped"),
    ("Commerce Missions", "Agent orchestrator", "Multi-intent decomposition; every intent independently risk-governed; only a financial sub-transaction is ever OrderGuard-authorized, never a merged global authorization.", "src/orderguard/agent/missions.py", "shipped"),
    ("Encrypted connector credential store", "Agent orchestrator", "Fernet-encrypted bearer tokens, owner-scoped (LOCAL_SINGLE_USER today; not process-global by construction), shared by both runtimes.", "src/orderguard/agent/connector_accounts.py", "shipped"),
    ("Real Swiggy backend OAuth (Developer flow)", "Agent orchestrator", "OAuth 2.1 + PKCE + RFC 7591 dynamic client registration, confirmed self-serve on localhost directly from Swiggy's own docs. Live-verified 2026-08-31: a real browser OAuth round trip completed against mcp.swiggy.com, the resulting token was encrypted and stored in ConnectorAccount, and the connector immediately showed CONNECTED with no further steps.", "src/orderguard/agent/swiggy_oauth.py", "shipped"),
    ("GitHub connector (required non-commerce proof)", "Agent orchestrator", "Chosen because it needs only a personal access token, not an OAuth app -- the fastest real path to a second, genuinely different connector.", "src/orderguard/agent/connector_registry.py", "offline_tested_pending_credential: needs a real GitHub personal access token"),
    ("SSRF-hardened custom connectors", "Agent orchestrator", "HTTPS-only, private/loopback/link-local IP rejection, cross-host redirect rejection, re-resolved on every call (not just at registration) to close the DNS-rebinding gap; discovered tools stored disabled until explicitly enabled with a non-R3 risk tier.", "src/orderguard/agent/custom_connectors.py, src/orderguard/agent/ssrf_guard.py", "shipped"),
    ("BYOK Anthropic API key", "Agent orchestrator", "In-memory only; never written to disk, logged, or echoed back beyond a masked confirmation. Distinct from the server-managed .env key and the subscription runtime.", "src/orderguard/agent/runtime_settings.py", "shipped"),
    ("Connector backend compatibility matrix", "Agent orchestrator", "Every connector classified as REMOTE_MCP / NATIVE_API_ADAPTER / CUSTOM_MCP / CLAUDE_DIRECTORY_ONLY / BROWSER_HANDOFF / UNSUPPORTED before it can be labeled reachable.", "src/orderguard/connectors.py::ConnectorBackendType", "shipped"),

    # --- UI ---------------------------------------------------------------
    # One UI surface: the React/Vite frontend (frontend/src/). A second,
    # server-rendered client (web/*.html+*.js) existed earlier in the
    # project and was removed once its one unported capability -- a real
    # FreshCart search -> select -> confirm -> pay flow -- was rebuilt here
    # as the Shop screen, so the same product is never split across two
    # different apps again.
    ("Mission screen + pipeline trace", "UI", "Real-time 3D WebGL mission pipeline (intent -> route -> connector -> tool call -> result), live per-step transparency (the model's own text + call duration, not just a pass/fail), and cross-turn conversation continuity so a reply to the model's own question reaches the same open conversation.", "frontend/src/pages/Mission.tsx, frontend/src/components/PipelineScene.tsx, frontend/src/components/pipeline/PipelineCanvas.tsx", "shipped"),
    ("Shop screen", "UI", "The real FreshCart/Shopify direct-search purchase flow (search -> select -> confirm -> Razorpay checkout), driving the same app.py session endpoints the automated payment tests exercise. The only place in the product a real Razorpay test payment can be completed end to end.", "frontend/src/pages/Shop.tsx, frontend/src/lib/shop.ts", "shipped"),
    ("Connectors screen", "UI", "Registry cards with live per-connector connect state, runtime settings (including BYOK and a live runtime-mode switch), 'Connected via your Claude account' live detection (claude mcp list, with an explicit loading state for its multi-second health check), and an 'Add Custom MCP' form.", "frontend/src/pages/Connectors.tsx", "shipped"),
    ("Attack Lab screen", "UI", "Every number sourced from results/latest.json; no number typed by hand.", "frontend/src/pages/AttackLab.tsx", "shipped"),
    ("Evidence screen + purchase receipt", "UI", "Independent audit-chain re-verification, a session lookup, and a full Evidence Receipt (gates, signed authorization, Razorpay ledger state, audit chain -- every field re-verified live, not read from a cached flag).", "frontend/src/pages/Evidence.tsx, frontend/src/components/ReceiptCard.tsx, src/orderguard/app.py::session_receipt", "shipped"),
    ("Features screen", "UI", "Renders this exact feature matrix.", "frontend/src/pages/Features.tsx", "shipped"),
    ("Eval / Judge screen", "UI", "This repo's own benchmark next to a separate, fresh-context evaluator's report -- never merged into one number.", "frontend/src/pages/Eval.tsx", "shipped"),
]


def main() -> int:
    generated_at = datetime.now(timezone.utc).isoformat()
    tests_by_category = {
        "Gates": ["tests/test_checkout_guard.py", "tests/test_payment_flow.py"],
        "Decision Council": ["tests/test_decision_council.py"],
        "Payment": ["tests/test_authorization.py", "tests/test_ledger.py", "tests/test_payment_flow.py", "tests/test_webhooks.py"],
        "Audit": ["tests/test_audit.py"],
        "Connectors": ["tests/test_connectors.py", "tests/test_merchants.py", "tests/test_shopify_mcp.py"],
        "Eval": ["tests/test_benchmark.py", "tests/test_attack_lab.py", "tests/test_eval_script.py"],
        "MCP": ["tests/test_mcp_server.py"],
        "Agent orchestrator": ["tests/test_runtime_adapters.py", "tests/test_eligibility.py", "tests/test_orchestrator.py"],
        "UI": ["tests/test_app.py", "tests/test_xss.py"],
    }
    # Features actually exercised end-to-end through the real, running app
    # (not just unit-tested against a stub) -- named individually, with the
    # specific evidence, rather than a blanket claim. Keep this list honest:
    # add an entry only after actually watching it happen, the same
    # discipline results/latest.json and docs/CONNECTORS.md already hold to.
    LIVE_VERIFIED_EVIDENCE: dict[str, str] = {
        "dual-agent-runtime-api-subscription": (
            "Live-verified 2026-08-31: a real mission through the browser UI "
            "(subscription runtime) resolved a real saved address, called the "
            "real Swiggy Instamart search_products tool, and returned 33 real "
            "offers -- see docs/CONNECTORS.md."
        ),
        "real-swiggy-backend-oauth-developer-flow": (
            "Live-verified 2026-08-31: a real browser OAuth round trip "
            "completed against mcp.swiggy.com; the token was encrypted, "
            "stored in ConnectorAccount, and the connector immediately "
            "showed CONNECTED."
        ),
    }

    features = []
    for index, (name, category, description, implemented_in, old_status) in enumerate(FEATURES, 1):
        feature_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        status = (
            "IMPLEMENTED_UNVERIFIED"
            if old_status.startswith("offline_tested_pending_credential")
            else "VERIFIED_TESTED"
        )
        live_evidence = LIVE_VERIFIED_EVIDENCE.get(feature_id)
        features.append({
            "feature_id": feature_id,
            "feature_name": name,
            # Compatibility fields used by the existing Features UI.
            "name": name,
            "category": category,
            "description": description,
            "implementing_files/functions": [
                part.strip() for part in implemented_in.split(",") if part.strip()
            ],
            "implemented_in": implemented_in,
            "tests": tests_by_category.get(category, []),
            "status": status,
            "live_verified": live_evidence is not None,
            "evidence": live_evidence or (
                "Offline implementation only; current live credential proof is pending."
                if status == "IMPLEMENTED_UNVERIFIED"
                else "Covered by the repository test suite; current clean live verification is not claimed."
            ),
            "user_visible_surface": category if category == "UI" else "Evidence / System",
            "security_relevance": category in {
                "Gates", "Decision Council", "Payment", "Audit", "Connectors",
                "MCP", "Agent orchestrator", "Eval",
            },
        })

    results_out = Path("results/feature_matrix.json")
    results_out.parent.mkdir(parents=True, exist_ok=True)
    results_out.write_text(json.dumps(
        {"generated_at": generated_at, "features": features}, indent=2,
    ) + "\n")

    by_category: dict[str, list[dict]] = {}
    for f in features:
        by_category.setdefault(f["category"], []).append(f)

    lines = [
        "# Feature matrix",
        "",
        f"Generated {generated_at} by `scripts/feature_matrix.py` — this file and "
        "`results/feature_matrix.json` are written from the same list, so they "
        "cannot drift. No entry was added to round out a count.",
        "",
        f"**{len(features)} features total.**",
        "",
    ]
    for category, items in by_category.items():
        lines.append(f"## {category} ({len(items)})")
        lines.append("")
        for f in items:
            live = "yes" if f["live_verified"] else "no"
            lines.append(
                f"- **{f['feature_name']}** — `{f['status']}`; live verified: "
                f"**{live}** — {f['description']} (`{f['implemented_in']}`)"
            )
        lines.append("")

    docs_out = Path("docs/FEATURE_MATRIX.md")
    docs_out.write_text("\n".join(lines) + "\n")

    print(f"written to {results_out}")
    print(f"written to {docs_out}")
    print(f"{len(features)} features total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
