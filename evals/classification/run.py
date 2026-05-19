"""Classification eval — runs deberta + classical + llm on `golden.jsonl`.

Emits `eval_report.json` with per-model accuracy, macro-F1, per-class F1,
confusion matrix, p50/p99 latency, and (for the LLM) cost-per-1k predictions.
Slice 15 (CI gate + baseline diff) consumes this report.

Usage:
    uv run --extra evals --extra api python -m evals.classification.run \\
        --out reports/classification.json \\
        --models deberta,classical,llm
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sklearn.metrics import (  # type: ignore[import-untyped]
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)

from evals.classification.baselines import classical as classical_b
from evals.classification.baselines import deberta as deberta_b

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")
DEFAULT_GOLDEN = Path("evals/classification/golden.jsonl")
DEFAULT_OUT = Path("reports/classification.json")


def _load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    return float(np.percentile(values, p))


def _per_class_f1(y_true: list[str], y_pred: list[str]) -> dict[str, dict]:
    report: dict = classification_report(  # pyright: ignore[reportAssignmentType]
        y_true,
        y_pred,
        labels=list(LABELS),
        target_names=list(LABELS),
        output_dict=True,
        zero_division=0,  # pyright: ignore[reportArgumentType]
    )
    return {
        label: {
            "f1": float(report[label]["f1-score"]),
            "support": int(report[label]["support"]),
        }
        for label in LABELS
    }


def _metrics(y_true: list[str], y_pred: list[str], latencies_ms: list[float]) -> dict:
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),  # type: ignore[arg-type]
        "per_class_f1": _per_class_f1(y_true, y_pred),
        "confusion": confusion_matrix(y_true, y_pred, labels=list(LABELS)).tolist(),
        "confusion_labels": list(LABELS),
        "p50_latency_ms": _percentile(latencies_ms, 50),
        "p99_latency_ms": _percentile(latencies_ms, 99),
        "n": len(y_true),
    }


def _run_deberta(records: list[dict]) -> dict:
    preds, lat = deberta_b.predict_batch(records)
    return {"predictions": preds, "latencies_ms": lat, "cost_per_1k_usd": 0.0}


def _run_classical(records: list[dict]) -> dict:
    if not classical_b.ARTIFACT_PATH.exists():
        print(
            f"classical artifact missing, fitting now ({classical_b.ARTIFACT_PATH})",
            file=sys.stderr,
        )
        classical_b.train_and_persist()
    texts = [classical_b.build_input(r.get("title"), r.get("body")) for r in records]
    preds, lat = classical_b.predict_batch(texts)
    return {"predictions": preds, "latencies_ms": lat, "cost_per_1k_usd": 0.0}


def _run_llm(records: list[dict]) -> dict:
    from evals.classification.baselines import llm as llm_b

    preds, lat, usage = llm_b.predict_batch(records)
    cost_per_1k = (usage["total_cost_usd"] / usage["n"]) * 1000 if usage["n"] else 0.0
    return {
        "predictions": preds,
        "latencies_ms": lat,
        "cost_per_1k_usd": cost_per_1k,
        "usage": usage,
    }


_RUNNERS = {
    "deberta": _run_deberta,
    "classical": _run_classical,
    "llm": _run_llm,
}


def run(
    golden_path: Path,
    out_path: Path,
    models: list[str],
) -> int:
    records = _load_jsonl(golden_path)
    y_true = [r["label"] for r in records]

    per_model: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for name in models:
        if name not in _RUNNERS:
            errors[name] = f"unknown model {name!r}"
            continue
        print(f"running {name} on {len(records)} records ...", file=sys.stderr)
        try:
            result = _RUNNERS[name](records)
        except Exception as exc:  # noqa: BLE001 — eval is best-effort per model
            errors[name] = f"{type(exc).__name__}: {exc}"
            print(f"  FAILED: {errors[name]}", file=sys.stderr)
            continue
        m = _metrics(y_true, result["predictions"], result["latencies_ms"])
        per_model[name] = {
            **m,
            "cost_per_1k_usd": result["cost_per_1k_usd"],
        }
        if "usage" in result:
            per_model[name]["usage"] = result["usage"]
        print(
            f"  {name}: macro_f1={m['macro_f1']:.4f} acc={m['accuracy']:.4f} "
            f"p50={m['p50_latency_ms']:.1f}ms",
            file=sys.stderr,
        )

    deployed = _pick_deployed(per_model)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "golden_set": {
            "path": str(golden_path),
            "sha256": _sha256(golden_path),
            "n": len(records),
            "per_class_support": _per_class_support(y_true),
        },
        "models": per_model,
        "deployed": deployed,
        "errors": errors,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}", file=sys.stderr)
    if errors:
        # Non-zero only if EVERY requested model failed; otherwise downstream
        # CI can still gate on whatever ran. Slice 15's gate is the source of truth.
        return 1 if not per_model else 0
    return 0


def _per_class_support(y_true: list[str]) -> dict[str, int]:
    return {label: y_true.count(label) for label in LABELS}


def _pick_deployed(per_model: dict[str, dict]) -> str | None:
    """Heuristic deployment choice: highest macro-F1, ties broken by lower p50 latency.

    Final defense in DECISIONS.md is a human call, not this function. The
    `deployed` field is informational and downstream code should not assume it.
    """
    if not per_model:
        return None
    ordered = sorted(
        per_model.items(),
        key=lambda kv: (-kv[1]["macro_f1"], kv[1]["p50_latency_ms"]),
    )
    return ordered[0][0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--models",
        type=str,
        default="deberta,classical,llm",
        help="comma-separated subset of {deberta,classical,llm}",
    )
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    return run(args.golden, args.out, models)


if __name__ == "__main__":
    sys.exit(main())
