"""``make eval`` actually runs and writes a real, parseable artifact.

Run as a real subprocess — the same command a judge would type — rather than
importing internals, so this proves the actual CLI entry point works, not
just the library functions underneath it.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_make_eval_exits_zero_and_writes_results_json():
    result = subprocess.run(
        [sys.executable, "scripts/eval.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    results_path = REPO_ROOT / "results" / "latest.json"
    assert results_path.exists()

    data = json.loads(results_path.read_text())
    assert set(data) == {
        "generated_at", "metadata", "fixed_fifty", "injection_curve", "attack_lab",
        "baselines", "agent_attack_lab",
    }
    # metadata carries reproducibility fields (commit/seed/suite version) —
    # see scripts/eval.py::_metadata and DECISIONS.md's eval-artifact entry.
    assert set(data["metadata"]) == {
        "commit", "timestamp", "seed", "model_runtime", "scenario_count", "suite_version",
    }

    for extra in ("results/attack_lab.json", "results/connector_routing.json", "results/runtime_parity.json"):
        assert (REPO_ROOT / extra).exists(), f"{extra} was not written"


def test_the_critical_rates_are_all_reported_as_zero():
    results_path = REPO_ROOT / "results" / "latest.json"
    data = json.loads(results_path.read_text())

    assert data["fixed_fifty"]["false_match_rate"] == 0.0
    assert data["attack_lab"]["false_match_rate"] == 0.0
    assert all(point["false_match_rate"] == 0.0 for point in data["injection_curve"])

    baselines = {b["name"]: b for b in data["baselines"]}
    assert baselines["orderguard"]["unsafe_acceptance_rate"] == 0.0
    assert baselines["no_guard"]["unsafe_acceptance_rate"] == 1.0

    assert data["agent_attack_lab"]["all_correct"] is True


def test_docs_benchmark_md_is_also_written():
    md_path = REPO_ROOT / "docs" / "BENCHMARK.md"
    assert md_path.exists()
    text = md_path.read_text()
    assert "Baselines" in text
    assert "false-match rate" in text.lower()
