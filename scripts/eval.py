"""Run every evidence artifact this project makes and write results/latest.json.

    make eval

No model calls, no network, no randomness that isn't seeded — same rule
test-offline already enforces on the test suite. This exists so a number
shown in a README or a demo screen can never drift from what was actually
measured: the UI (or a judge, by hand) reads results/latest.json, and this
script is the only thing allowed to write it.
"""

from __future__ import annotations

import json
import asyncio
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orderguard.benchmark import (  # noqa: E402
    render_baselines_markdown,
    render_injection_markdown,
    render_markdown,
    run_attack_lab,
    run_baselines,
    run_benchmark,
    run_injection_curve,
)
from orderguard.agent.attack_lab import run_agent_attack_lab  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from claude_agent_sdk import (  # noqa: E402
    AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage,
)
from orderguard.agent.connector_accounts import (  # noqa: E402
    ConnectorAccountStore, accounts_engine,
)
from orderguard.agent.eligibility import ConnectorEligibilityEngine  # noqa: E402
from orderguard.agent.normalizer import normalize  # noqa: E402
from orderguard.agent.runtime.api_runtime import AnthropicApiRuntime  # noqa: E402
from orderguard.agent.runtime.subscription_runtime import SubscriptionAgentRuntime  # noqa: E402
from orderguard.agent.tools import (  # noqa: E402
    ConnectorInvocationSpec, FinancialToolExposureError, ToolPermission,
)


_SEED = 20260831
_SUITE_VERSION = "2.0.0"


def _metadata(*, scenario_count: int, runtime: str) -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False,
    ).stdout.strip() or "unknown"
    return {
        "commit": commit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "seed": _SEED,
        "model_runtime": runtime,
        "scenario_count": scenario_count,
        "suite_version": _SUITE_VERSION,
    }


def _routing_report() -> dict:
    def store() -> ConnectorAccountStore:
        return ConnectorAccountStore(
            accounts_engine(":memory:"), fernet=Fernet(Fernet.generate_key()),
        )

    scenarios: list[dict] = []

    def record(name: str, expected: list[str], actual: list[str]) -> None:
        scenarios.append({
            "name": name, "expected_connector_ids": expected,
            "actual_connector_ids": actual, "correct": actual == expected,
        })

    plain = store()
    engine = ConnectorEligibilityEngine(plain)
    record("public_shopify_read", ["shopify"], [
        c.id for c in engine.eligible_for("COMMERCE_GENERAL")
    ])
    record("github_disconnected", [], [c.id for c in engine.eligible_for("DEV_TASK")])
    record("claude_cli_session_not_credential", [], [
        c.id for c in engine.eligible_for(
            "COMMERCE_GROCERY",
            cli_connected_ids=frozenset({"swiggy-instamart"}),
            runtime_name="subscription",
        )
    ])

    github = store()
    github.store_token("github", "fixture-token", expires_in_seconds=None)
    record("github_connected_owner", ["github"], [
        c.id for c in ConnectorEligibilityEngine(github).eligible_for("DEV_TASK")
    ])

    expired = store()
    expired.store_token("github", "fixture-token", expires_in_seconds=-1)
    record("github_expired", [], [
        c.id for c in ConnectorEligibilityEngine(expired).eligible_for("DEV_TASK")
    ])

    region = store()
    region.store_token("swiggy-instamart", "fixture-token", expires_in_seconds=None)
    record("swiggy_wrong_region", [], [
        c.id for c in ConnectorEligibilityEngine(region).eligible_for(
            "COMMERCE_GROCERY", region="US",
        )
    ])

    correct = sum(1 for scenario in scenarios if scenario["correct"])
    return {
        "metadata": _metadata(scenario_count=len(scenarios), runtime="deterministic-offline"),
        "metrics": {
            "connector_routing_accuracy": correct / len(scenarios),
            "invalid_connector_selection_rate": (len(scenarios) - correct) / len(scenarios),
        },
        "scenarios": scenarios,
    }


async def _runtime_parity_report() -> dict:
    raw_result = [{
        "type": "text",
        "text": '{"issues":[{"number":5,"title":"Parity","state":"open","html_url":"https://github.com/x/y/issues/5","user":{"login":"octo"}}]}',
    }]
    response = SimpleNamespace(
        id="msg-api", stop_reason="end_turn", usage={"input_tokens": 10},
        content=[
            SimpleNamespace(
                type="mcp_tool_use", id="tool-api", server_name="github",
                name="list_issues", input={"owner": "x", "repo": "y"},
            ),
            SimpleNamespace(
                type="mcp_tool_result", tool_use_id="tool-api",
                content=raw_result, is_error=False,
            ),
        ],
    )
    fake_client = MagicMock()
    fake_client.beta.messages.create.return_value = response

    async def fake_query(*, prompt, options):
        yield AssistantMessage(
            content=[ToolUseBlock(
                id="tool-sub", name="mcp__github__list_issues",
                input={"owner": "x", "repo": "y"},
            )],
            model="claude-test", stop_reason="tool_use",
        )
        yield UserMessage(content=[ToolResultBlock(
            tool_use_id="tool-sub", content=raw_result, is_error=False,
        )])

    spec = ConnectorInvocationSpec(
        connector_id="github", url="https://api.githubcopilot.com/mcp/",
        tools=(ToolPermission("list_issues", "R0"),), bearer_token="fixture-token",
    )
    api = AnthropicApiRuntime(api_key="fixture-api-key", client=fake_client)
    subscription = SubscriptionAgentRuntime(
        oauth_token="fixture-oauth-token", query_fn=fake_query,
    )
    api_turn = await api.run_turn("system", "user", [spec])
    sub_turn = await subscription.run_turn("system", "user", [spec])
    api_call, sub_call = api_turn.tool_calls[0], sub_turn.tool_calls[0]
    semantic_fields_equal = (
        api_call.connector_id, api_call.tool_name, api_call.arguments,
        api_call.result, api_call.succeeded,
    ) == (
        sub_call.connector_id, sub_call.tool_name, sub_call.arguments,
        sub_call.result, sub_call.succeeded,
    )
    normalized_equal = normalize(
        api_call, capability="DEV_TASK", risk_tier="R0", provenance="api:github",
    ).payload == normalize(
        sub_call, capability="DEV_TASK", risk_tier="R0",
        provenance="subscription:github",
    ).payload

    r3_spec = ConnectorInvocationSpec(
        connector_id="github", url="https://api.githubcopilot.com/mcp/",
        tools=(ToolPermission("pay", "R3", "FINANCIAL"),),
    )
    r3_rejections = []
    for runtime in (api, subscription):
        try:
            await runtime.run_turn("system", "user", [r3_spec])
            r3_rejections.append(False)
        except FinancialToolExposureError:
            r3_rejections.append(True)

    scenarios = [
        {"name": "semantic_tool_result_parity", "correct": semantic_fields_equal},
        {"name": "normalized_payload_parity", "correct": normalized_equal},
        {"name": "r3_rejection_parity", "correct": all(r3_rejections)},
    ]
    correct = sum(1 for scenario in scenarios if scenario["correct"])
    return {
        "metadata": _metadata(scenario_count=len(scenarios), runtime="api+subscription-offline-fixtures"),
        "metrics": {"runtime_parity": correct / len(scenarios)},
        "scenarios": scenarios,
    }


def main() -> int:
    report = run_benchmark()
    curve = run_injection_curve()
    attack_lab = run_attack_lab()
    baselines = run_baselines(report.journeys)
    agent_attack_lab = run_agent_attack_lab()
    routing = _routing_report()
    runtime_parity = asyncio.run(_runtime_parity_report())

    docs_out = Path("docs/BENCHMARK.md")
    docs_out.write_text(
        render_markdown(report) + "\n" + render_injection_markdown(curve)
        + "\n" + render_baselines_markdown(baselines)
    )

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": _metadata(
            scenario_count=report.total + attack_lab.total + agent_attack_lab.total,
            runtime="deterministic-offline",
        ),
        "fixed_fifty": {
            "total": report.total,
            "match_rate": report.match_rate,
            "false_match_rate": report.false_match_rate,
            "false_block_rate": report.false_block_rate,
            "duplicate_business_effects": report.duplicate_business_effects,
            "p50_ms": report.p50_ms,
            "p95_ms": report.p95_ms,
            "by_category": report.by_category,
        },
        "injection_curve": [
            {
                "rate": p.rate, "n": p.n,
                "false_match_rate": p.false_match_rate,
                "false_block_rate": p.false_block_rate,
            }
            for p in curve
        ],
        "attack_lab": {
            "total": attack_lab.total,
            "match_rate": attack_lab.match_rate,
            "false_match_rate": attack_lab.false_match_rate,
            "false_block_rate": attack_lab.false_block_rate,
            "scenarios": [
                {
                    "kind": j.kind.value, "should_allow": j.should_allow,
                    "allowed": j.allowed, "correct": j.correct, "note": j.note,
                }
                for j in attack_lab.journeys
            ],
        },
        "baselines": [
            {
                "name": b.name, "description": b.description,
                "unsafe_acceptance_rate": b.unsafe_acceptance_rate,
                "valid_acceptance_rate": b.valid_acceptance_rate,
                "unsafe_acceptance_count": b.unsafe_acceptance_count,
                "total_attacks": b.total_attacks,
                "leaked_amount_paise": b.leaked_amount_paise,
                "total_exposed_paise": b.total_exposed_paise,
            }
            for b in baselines
        ],
        # A different layer of attack than the fixed fifty / attack_lab above:
        # those defend a CART against evaluate_pre_payment_gates; these attack
        # the agent orchestrator's own invariants (tool exposure, connector
        # eligibility, SSRF, mission independence) — see
        # src/orderguard/agent/attack_lab.py's module docstring for why they
        # are not forced through the same cart-shaped harness.
        "agent_attack_lab": {
            "total": agent_attack_lab.total,
            "all_correct": agent_attack_lab.all_correct,
            "scenarios": [
                {"kind": r.kind, "should_block": r.should_block, "blocked": r.blocked, "correct": r.correct, "note": r.note}
                for r in agent_attack_lab.results
            ],
        },
    }

    results_out = Path("results/latest.json")
    results_out.parent.mkdir(parents=True, exist_ok=True)
    results_out.write_text(json.dumps(results, indent=2) + "\n")

    attack_out = Path("results/attack_lab.json")
    attack_out.write_text(json.dumps({
        "metadata": _metadata(
            scenario_count=attack_lab.total + agent_attack_lab.total,
            runtime="deterministic-offline",
        ),
        "payment_gate_attack_lab": results["attack_lab"],
        "agent_control_plane_attack_lab": results["agent_attack_lab"],
    }, indent=2) + "\n")
    routing_out = Path("results/connector_routing.json")
    routing_out.write_text(json.dumps(routing, indent=2) + "\n")
    parity_out = Path("results/runtime_parity.json")
    parity_out.write_text(json.dumps(runtime_parity, indent=2) + "\n")

    print(f"written to {docs_out}")
    print(f"written to {results_out}")
    print(f"written to {attack_out}")
    print(f"written to {routing_out}")
    print(f"written to {parity_out}")

    # The numbers that must never be nonzero, all in one place. Non-zero exit
    # so this can gate CI later, not just print a nice table.
    worst_curve_rate = max(p.false_match_rate for p in curve)
    problems = [
        ("fixed-fifty false-match rate", report.false_match_rate),
        ("injection-curve worst false-match rate", worst_curve_rate),
        ("attack-lab false-match rate", attack_lab.false_match_rate),
    ]
    failed = [(name, rate) for name, rate in problems if rate > 0]
    for name, rate in failed:
        print(f"FAILED: {name} is {rate:.0%}, must be 0%")

    if not agent_attack_lab.all_correct:
        wrong = [r.kind for r in agent_attack_lab.results if not r.correct]
        print(f"FAILED: agent attack lab scenarios not correctly handled: {wrong}")
        failed.append(("agent attack lab", 1.0))

    if routing["metrics"]["connector_routing_accuracy"] != 1.0:
        print("FAILED: connector routing accuracy is not 100%")
        failed.append(("connector routing", 1.0))
    if runtime_parity["metrics"]["runtime_parity"] != 1.0:
        print("FAILED: API/subscription semantic runtime parity is not 100%")
        failed.append(("runtime parity", 1.0))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
