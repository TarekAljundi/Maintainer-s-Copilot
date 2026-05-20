"""RAG retrieval eval — deterministic hit@5, MRR@10 (slice 06).

Slice 06: retrieval-only metrics, no generation eval (RAGAS lands in 13).
- hit@5  = fraction of questions whose top-5 dense hits intersect the GT set.
- MRR@10 = mean reciprocal rank of the first GT chunk within top-10 (0 if absent).

Usage:
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python -m evals.rag.run [--golden evals/rag/golden.jsonl] [--out reports/rag_eval.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rag_eval")


def hit_at_k(retrieved_ids: list[str], gt_ids: set[str], k: int) -> int:
    return 1 if any(rid in gt_ids for rid in retrieved_ids[:k]) else 0


def reciprocal_rank(retrieved_ids: list[str], gt_ids: set[str], k: int) -> float:
    for rank, rid in enumerate(retrieved_ids[:k], start=1):
        if rid in gt_ids:
            return 1.0 / rank
    return 0.0


async def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=Path("evals/rag/golden.jsonl"), type=Path)
    parser.add_argument("--out", default=Path("reports/rag_eval.json"), type=Path)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--stack", default="naive (dense only)")
    args = parser.parse_args()

    if not args.golden.exists():
        log.error("missing golden file: %s", args.golden)
        return 1

    golden = [
        json.loads(line)
        for line in args.golden.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    log.info("loaded %d golden records", len(golden))

    # Use the local Embedder directly so the eval doesn't require model-server up.
    # Same model + same prefix as runtime; retrieval SQL is identical via chunks_repo.
    from app.repositories import chunks as chunks_repo
    from model_server.embedder import Embedder

    log.info("loading bge-base embedder...")
    embedder = Embedder()
    questions = [r["question"] for r in golden if (r.get("ground_truth_chunk_ids") or [])]
    log.info("embedding %d questions...", len(questions))
    q_embeddings = embedder.encode(questions, mode="query")
    q_iter = iter(q_embeddings)

    per_question = []
    t0 = time.time()
    for i, rec in enumerate(golden, 1):
        q = rec["question"]
        gt = set(rec.get("ground_truth_chunk_ids") or [])
        if not gt:
            log.warning("skipping (no GT): %s", q[:60])
            continue
        emb = next(q_iter)
        hits = await chunks_repo.dense_search(emb, top_k=args.top_k)
        retrieved = [h.id for h in hits]
        h5 = hit_at_k(retrieved, gt, 5)
        rr10 = reciprocal_rank(retrieved, gt, 10)
        per_question.append(
            {
                "question": q,
                "tags": rec.get("tags", []),
                "gt_chunk_ids": sorted(gt),
                "retrieved_top10": retrieved,
                "hit_at_5": h5,
                "rr_at_10": rr10,
            }
        )
        log.info("  [%2d/%d] hit@5=%d rr@10=%.3f  %s", i, len(golden), h5, rr10, q[:60])

    elapsed = time.time() - t0
    hit5 = statistics.mean(r["hit_at_5"] for r in per_question)
    mrr10 = statistics.mean(r["rr_at_10"] for r in per_question)

    # By tag (docs vs issue)
    by_tag: dict[str, dict[str, float]] = {}
    for tag in ("docs", "issue"):
        subset = [r for r in per_question if tag in r["tags"]]
        if subset:
            by_tag[tag] = {
                "n": len(subset),
                "hit_at_5": statistics.mean(r["hit_at_5"] for r in subset),
                "mrr_at_10": statistics.mean(r["rr_at_10"] for r in subset),
            }

    report = {
        "stack": args.stack,
        "n": len(per_question),
        "hit_at_5": hit5,
        "mrr_at_10": mrr10,
        "by_tag": by_tag,
        "elapsed_s": round(elapsed, 1),
        "per_question": per_question,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))

    log.info("=" * 60)
    log.info("stack:    %s", args.stack)
    log.info("n:        %d", len(per_question))
    log.info("hit@5:    %.3f", hit5)
    log.info("MRR@10:   %.3f", mrr10)
    for tag, m in by_tag.items():
        log.info("  %s (n=%d)  hit@5=%.3f  MRR@10=%.3f", tag, m["n"], m["hit_at_5"], m["mrr_at_10"])
    log.info("elapsed:  %.1fs", elapsed)
    log.info("report:   %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
