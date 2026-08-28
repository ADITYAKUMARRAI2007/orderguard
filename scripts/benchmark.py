"""Run the fifty-journey benchmark and write docs/BENCHMARK.md.

    make benchmark

Reproducible: no randomness, no network. Same code path as the running app.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orderguard.benchmark import render_markdown, run_benchmark  # noqa: E402


def main() -> int:
    report = run_benchmark()
    text = render_markdown(report)

    out = Path("docs/BENCHMARK.md")
    out.write_text(text)

    print(text)
    print(f"written to {out}")

    # Non-zero exit if the one number that must never be nonzero is nonzero —
    # so this can gate CI later, not just print a nice table.
    return 1 if report.false_match_rate > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
