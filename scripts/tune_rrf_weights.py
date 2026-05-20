"""Tune RRF dense/sparse weights on the 25-Q golden set.

Sweep `{(1.0,0.5),(1.0,1.0),(1.0,1.5),(0.5,1.0)}` (PRD Q12), score by MRR@10
(tiebreak hit@5). Winner is printed and dumped to reports/rrf_grid.json; the
chosen (w_d, w_s) belongs in app/services/rag.py constants + DECISIONS.md.

Usage (with compose db running on localhost:5432):
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python scripts/tune_rrf_weights.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tune_rrf")

GRID: list[tuple[float, float]] = [(1.0, 0.5), (1.0, 1.0), (1.0, 1.5), (0.5, 1.0)]
GOLDEN_PATH = Path("evals/rag/golden.jsonl")
REPORT_PATH = Path("reports/rrf_grid.json")
OVERFETCH = 200


async def run() -> int:
    if not GOLDEN_PATH.exists():
        log.error("missing golden file: %s", GOLDEN_PATH)
        return 1

    golden = [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = [g for g in golden if g.get("ground_truth_chunk_ids")]
    log.info("loaded %d golden records", len(golden))

    from app.repositories import chunks as chunks_repo
    from app.services.rag import _rrf_fuse
    from model_server.embedder import Embedder

    log.info("loading bge-base embedder...")
    embedder = Embedder()
    questions = [g["question"] for g in golden]
    log.info("embedding %d questions...", len(questions))
    q_embeddings = embedder.encode(questions, mode="query")

    # Pre-fetch dense + sparse top-200 once per question (independent of weights).
    log.info("pre-fetching dense + sparse candidates (top-%d per side)...", OVERFETCH)
    t0 = time.time()
    per_question: list[dict] = []
    for g, emb in zip(golden, q_embeddings):
        dense = await chunks_repo.dense_search(emb, top_k=OVERFETCH)
        sparse = await chunks_repo.fts_search(g["question"], top_k=OVERFETCH)
        per_question.append(
            {
                "question": g["question"],
                "gt": set(g["ground_truth_chunk_ids"]),
                "dense": dense,
                "sparse": sparse,
            }
        )
    log.info("candidates fetched in %.1fs", time.time() - t0)

    results = []
    for w_d, w_s in GRID:
        hits5 = []
        rrs10 = []
        for q in per_question:
            fused = _rrf_fuse(q["dense"], q["sparse"], w_d=w_d, w_s=w_s)
            top10 = [c.id for c in fused[:10]]
            top5 = top10[:5]
            hits5.append(1 if any(i in q["gt"] for i in top5) else 0)
            rr = 0.0
            for rank, cid in enumerate(top10, start=1):
                if cid in q["gt"]:
                    rr = 1.0 / rank
                    break
            rrs10.append(rr)
        h5 = statistics.mean(hits5)
        mrr = statistics.mean(rrs10)
        results.append({"w_d": w_d, "w_s": w_s, "hit_at_5": h5, "mrr_at_10": mrr})
        log.info("  w_d=%.1f w_s=%.1f  hit@5=%.3f  MRR@10=%.3f", w_d, w_s, h5, mrr)

    winner = max(results, key=lambda r: (r["mrr_at_10"], r["hit_at_5"]))
    log.info(
        "WINNER: w_d=%.1f w_s=%.1f (MRR@10=%.3f, hit@5=%.3f)",
        winner["w_d"],
        winner["w_s"],
        winner["mrr_at_10"],
        winner["hit_at_5"],
    )

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps({"grid": results, "winner": winner}, indent=2), encoding="utf-8"
    )
    log.info("report: %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
