"""Run the real backend test suite and write results/test_report.json.

Same rule as feature_matrix.py and eval.py: the UI reads this file, never a
hand-typed count, so a number here is never able to drift from what the
suite actually reported the last time this ran.

    make test-report
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "results" / "test_report.json"

# Pytest's own final summary line is not reliably present across versions/
# configurations (observed missing here even on a clean, all-passing run) —
# counting the per-test result characters on its "-q" progress lines is not.
# Each of those lines is result characters followed by a "[NN%]" marker;
# every other character on the line is a single test's outcome.
_PROGRESS_LINE = re.compile(r"^([.FEsx]+)\s*\[\s*\d+%\]$")


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> None:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    wall_seconds = time.monotonic() - started

    output = result.stdout + result.stderr
    outcomes = ""
    for line in output.splitlines():
        match = _PROGRESS_LINE.match(line.strip())
        if match:
            outcomes += match.group(1)

    passed = outcomes.count(".") + outcomes.count("x")  # x = expected failure, still a passing run
    failed = outcomes.count("F") + outcomes.count("E")
    skipped = outcomes.count("s")
    summary_line = f"{passed} passed, {failed} failed, {skipped} skipped in {wall_seconds:.2f}s"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
        "exit_code": result.returncode,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": passed + failed + skipped,
        "duration_s": round(wall_seconds, 2),
        "summary_line": summary_line.strip(),
    }
    OUT_PATH.write_text(json.dumps(report, indent=2) + "\n")
    print(f"written to {OUT_PATH.relative_to(REPO_ROOT)}")
    print(report["summary_line"] or f"{passed} passed, {failed} failed")
    if result.returncode != 0:
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
