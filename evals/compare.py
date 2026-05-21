"""Merge classification + RAG reports, diff against the MinIO baseline, write `eval_report.json`.

Baseline lives at `s3://mc-evals/main/latest.json` (replaced by `evals.promote`
after every green main build). With `--bootstrap`, a missing baseline PASSES
the diff check and the current run becomes the seed — used on the first-ever
main build.

Usage:
    uv run python -m evals.compare \
        --classification reports/classification.json \
        --rag reports/rag_full.json \
        --thresholds eval_thresholds.yaml \
        --out reports/eval_report.json \
        [--bootstrap]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

BASELINE_KEY = "main/latest.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fetch_baseline() -> dict[str, Any] | None:
    try:
        from app.infra.minio import MinIOClient

        client = MinIOClient()
        raw = client.get_bytes(BASELINE_KEY)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — never let baseline fetch break the run
        print(f"baseline fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _extract_metrics(classification: dict, rag: dict) -> dict[str, float]:
    """Pull the metrics gated by `eval_thresholds.yaml` into a flat dict.

    Classification picks the `deployed` model — `eval_thresholds.yaml` floors
    apply to whichever model we shipped, not to all three.
    """
    out: dict[str, float] = {}
    deployed = classification.get("deployed")
    models = classification.get("models") or {}
    if deployed and deployed in models:
        m = models[deployed]
        out["classification.macro_f1"] = float(m.get("macro_f1", 0.0))
        for label, scores in (m.get("per_class_f1") or {}).items():
            out[f"classification.per_class_f1.{label}"] = float(scores.get("f1", 0.0))

    out["rag.hit_at_5"] = float(rag.get("hit_at_5", 0.0))
    out["rag.mrr_at_10"] = float(rag.get("mrr_at_10", 0.0))
    # Generation metrics are produced by RAGAS (separate workflow). Carry them
    # through when present so the gate can evaluate them; absent metrics are
    # simply not gated this run.
    for k in ("faithfulness", "answer_relevancy"):
        if k in rag:
            out[f"rag.{k}"] = float(rag[k])
    return out


def _flatten_thresholds(thresholds: dict) -> dict[str, dict[str, float]]:
    flat: dict[str, dict[str, float]] = {}
    cls = thresholds.get("classification") or {}
    if "macro_f1" in cls:
        flat["classification.macro_f1"] = cls["macro_f1"]
    for label, spec in (cls.get("per_class_f1") or {}).items():
        flat[f"classification.per_class_f1.{label}"] = spec
    for k, spec in (thresholds.get("rag") or {}).items():
        flat[f"rag.{k}"] = spec
    return flat


def _diff(
    current: dict[str, float],
    baseline: dict[str, float] | None,
    thresholds: dict[str, dict[str, float]],
) -> list[dict]:
    rows: list[dict] = []
    for metric, value in current.items():
        spec = thresholds.get(metric) or {}
        floor = float(spec.get("floor", 0.0))
        margin = float(spec.get("regression_margin", 0.0))
        base = None if baseline is None else baseline.get(metric)
        floor_ok = value >= floor
        regression_ok = base is None or value >= base - margin
        rows.append(
            {
                "metric": metric,
                "current": round(value, 4),
                "baseline": None if base is None else round(float(base), 4),
                "floor": floor,
                "regression_margin": margin,
                "floor_ok": bool(floor_ok),
                "regression_ok": bool(regression_ok),
                "passed": bool(floor_ok and regression_ok),
            }
        )
    return rows


def run(
    classification_path: Path,
    rag_path: Path,
    thresholds_path: Path,
    out_path: Path,
    bootstrap: bool,
) -> int:
    classification = _load_json(classification_path)
    rag = _load_json(rag_path)
    thresholds = _flatten_thresholds(_load_yaml(thresholds_path))

    current = _extract_metrics(classification, rag)
    baseline_report = _fetch_baseline()
    baseline_metrics = (baseline_report or {}).get("metrics") if baseline_report else None
    if baseline_metrics is None and not bootstrap:
        print("no baseline found and --bootstrap not set; treating as bootstrap", file=sys.stderr)
    rows = _diff(current, baseline_metrics, thresholds)

    overall_passed = all(r["passed"] for r in rows)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "bootstrap": bootstrap and baseline_metrics is None,
        "baseline_present": baseline_metrics is not None,
        "metrics": current,
        "diff": rows,
        "passed": overall_passed,
        "sources": {
            "classification": str(classification_path),
            "rag": str(rag_path),
            "thresholds": str(thresholds_path),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out_path}  passed={overall_passed}  bootstrap={report['bootstrap']}",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--classification", type=Path, default=Path("reports/classification.json"))
    p.add_argument("--rag", type=Path, default=Path("reports/rag_full.json"))
    p.add_argument("--thresholds", type=Path, default=Path("eval_thresholds.yaml"))
    p.add_argument("--out", type=Path, default=Path("reports/eval_report.json"))
    p.add_argument(
        "--bootstrap",
        action="store_true",
        help="pass the regression check when no baseline exists yet",
    )
    args = p.parse_args()
    return run(args.classification, args.rag, args.thresholds, args.out, args.bootstrap)


if __name__ == "__main__":
    sys.exit(main())
