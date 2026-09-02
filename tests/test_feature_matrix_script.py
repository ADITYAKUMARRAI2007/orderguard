"""``make feature-matrix`` actually runs and writes real, parseable
artifacts from one shared list — run as a real subprocess, matching
``test_eval_script.py``'s own rule for proving the CLI entry point works.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_it_exits_zero_and_writes_both_artifacts():
    result = subprocess.run(
        [sys.executable, "scripts/feature_matrix.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    json_path = REPO_ROOT / "results" / "feature_matrix.json"
    md_path = REPO_ROOT / "docs" / "FEATURE_MATRIX.md"
    assert json_path.exists()
    assert md_path.exists()


def test_every_feature_names_a_real_file_that_exists():
    data = json.loads((REPO_ROOT / "results" / "feature_matrix.json").read_text())
    assert data["features"], "the feature list must not be empty"
    for feature in data["features"]:
        for path in feature["implemented_in"].split(","):
            path = path.split("::")[0].strip()
            assert (REPO_ROOT / path).exists(), f"{feature['name']!r} cites a path that does not exist: {path!r}"


def test_no_feature_is_silently_claimed_shipped_without_a_real_credential_caveat_where_one_is_needed():
    """The three agent-runtime features that genuinely need a credential
    this repo cannot supply itself must say so, not claim "shipped"."""
    data = json.loads((REPO_ROOT / "results" / "feature_matrix.json").read_text())
    needs_credential = {"Dual agent runtime (API + subscription)", "Real Swiggy backend OAuth (Developer flow)", "GitHub connector (required non-commerce proof)"}
    for feature in data["features"]:
        if feature["name"] in needs_credential:
            assert feature["status"] != "shipped"


def test_the_markdown_and_json_report_the_same_total():
    json_path = REPO_ROOT / "results" / "feature_matrix.json"
    md_path = REPO_ROOT / "docs" / "FEATURE_MATRIX.md"
    data = json.loads(json_path.read_text())
    assert f"{len(data['features'])} features total" in md_path.read_text()
