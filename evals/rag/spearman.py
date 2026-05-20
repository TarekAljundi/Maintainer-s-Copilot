"""Spearman ρ between hand-labels and RAGAS answer_relevancy on 5/25 questions.

Loads `evals/rag/hand_labels.csv` and `reports/ragas.json`, joins on `question`,
computes Spearman ρ via scipy.stats.spearmanr.

Judge disagreement protocol (PRD §Evaluation): if ρ < 0.6 after the user
fills `human_score`, the answer_relevancy metric is demoted to `advisory: true`
in eval_thresholds.yaml and a note is added to EVALS.md.

Usage:
    python -m evals.rag.spearman \\
        [--labels evals/rag/hand_labels.csv] \\
        [--ragas reports/ragas.json] \\
        [--metric answer_relevancy]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("spearman")

ADVISORY_THRESHOLD = 0.6


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default=Path("evals/rag/hand_labels.csv"), type=Path)
    parser.add_argument("--ragas", default=Path("reports/ragas.json"), type=Path)
    parser.add_argument(
        "--metric", default="answer_relevancy", choices=["faithfulness", "answer_relevancy"]
    )
    parser.add_argument("--out", default=Path("reports/spearman.json"), type=Path)
    args = parser.parse_args()

    if not args.labels.exists():
        log.error("missing hand-labels CSV: %s", args.labels)
        return 1
    if not args.ragas.exists():
        log.error("missing RAGAS report: %s", args.ragas)
        return 1

    with args.labels.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    labeled = []
    for r in rows:
        score = (r.get("human_score") or "").strip()
        if not score:
            log.warning("skipping unscored row: %s", r["question"][:60])
            continue
        labeled.append({"question": r["question"], "human_score": float(score)})

    if len(labeled) < 2:
        log.error(
            "need at least 2 filled human_score rows to compute Spearman ρ (got %d)", len(labeled)
        )
        return 1

    ragas = json.loads(args.ragas.read_text(encoding="utf-8"))
    by_question = {r["question"]: r[args.metric] for r in ragas["per_question"]}

    pairs = []
    for r in labeled:
        judge = by_question.get(r["question"])
        if judge is None:
            log.warning("question not in RAGAS report: %s", r["question"][:60])
            continue
        pairs.append((r["human_score"], float(judge)))
    if len(pairs) < 2:
        log.error("no overlapping questions between hand-labels and RAGAS report")
        return 1

    from scipy.stats import spearmanr

    h = [p[0] for p in pairs]
    j = [p[1] for p in pairs]
    rho_result = spearmanr(h, j)
    rho = float(rho_result.statistic)  # type: ignore[union-attr]
    pval = float(rho_result.pvalue)  # type: ignore[union-attr]

    advisory = rho < ADVISORY_THRESHOLD

    out = {
        "metric": args.metric,
        "n": len(pairs),
        "spearman_rho": rho,
        "p_value": pval,
        "advisory_threshold": ADVISORY_THRESHOLD,
        "advisory": advisory,
        "pairs": [{"human": h_, "judge": j_} for h_, j_ in pairs],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))

    log.info("metric:        %s", args.metric)
    log.info("n:             %d", len(pairs))
    log.info("Spearman ρ:    %.3f  (p=%.3f)", rho, pval)
    log.info("advisory:      %s (threshold ρ < %.2f)", advisory, ADVISORY_THRESHOLD)
    log.info("report:        %s", args.out)
    if advisory:
        log.warning(
            "ρ below threshold — demote `%s` to `advisory: true` in eval_thresholds.yaml",
            args.metric,
        )
    return 0


if __name__ == "__main__":
    sys.exit(run())
