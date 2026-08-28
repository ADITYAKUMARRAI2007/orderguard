"""Run the fifty-journey benchmark and write docs/BENCHMARK.md.

    make benchmark

Reproducible: no randomness, no network. Same code path as the running app.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from orderguard.benchmark import (  # noqa: E402
    render_injection_markdown,
    render_markdown,
    run_benchmark,
    run_injection_curve,
)


def main() -> int:
    report = run_benchmark()
    curve = run_injection_curve()

    text = render_markdown(report) + "\n" + render_injection_markdown(curve)

    out = Path("docs/BENCHMARK.md")
    out.write_text(text)

    print(text)
    print(f"written to {out}")

    # Non-zero exit if the one number that must never be nonzero is nonzero —
    # so this can gate CI later, not just print a nice table.
    worst_curve_rate = max(p.false_match_rate for p in curve)
    return 1 if (report.false_match_rate > 0 or worst_curve_rate > 0) else 0


if __name__ == "__main__":
    sys.exit(main())
