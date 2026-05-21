"""CI gate. Reads the merged `eval_report.json` from `evals.compare` and exits
non-zero if any metric failed the dual-gate. Prints a per-metric table so the
failure is visible directly in the GitHub Actions log.

Usage:
    uv run python -m evals.gate reports/eval_report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.4f}"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m evals.gate <eval_report.json>", file=sys.stderr)
        return 2
    report = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    rows = report.get("diff") or []
    print(f"{'metric':45} {'current':>10} {'baseline':>10} {'floor':>8} {'margin':>8}  status")
    failed = []
    for r in rows:
        status = (
            "PASS" if r["passed"] else ("FAIL(floor)" if not r["floor_ok"] else "FAIL(regression)")
        )
        print(
            f"{r['metric']:45} {_fmt(r['current']):>10} {_fmt(r['baseline']):>10} "
            f"{r['floor']:>8.2f} {r['regression_margin']:>8.2f}  {status}"
        )
        if not r["passed"]:
            failed.append(r)

    if report.get("bootstrap"):
        print("bootstrap run — no baseline existed; regression check is a no-op.", file=sys.stderr)
    if failed:
        print(f"\nFAIL: {len(failed)}/{len(rows)} metrics did not pass dual-gate.", file=sys.stderr)
        return 1
    print(f"\nOK: all {len(rows)} metrics passed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
