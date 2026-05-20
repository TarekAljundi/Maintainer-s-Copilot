"""RAG retrieval eval — deterministic hit@5, MRR@10.

Slice 06: dense-only.
Slice 07: --stack {naive,hybrid,hybrid_rerank,full} for the cumulative table.

Uses a local in-process model-server replacement so the eval doesn't depend on
the docker compose model-server container (which loads classifier + NER too).
HyDE calls Groq directly with GROQ_API_KEY from env (vault-then-env fallback
lives in app/services/hyde._api_key).

Usage:
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python -m evals.rag.run [--stack STACK] [--golden FILE] [--out FILE]
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

STACKS = ("naive", "hybrid", "hybrid_rerank", "full")


class _LocalModelServer:
    """Stand-in for ModelServerClient that runs Embedder + Reranker locally.

    Loaded lazily so a `naive`-only eval doesn't pay the reranker download.
    """

    def __init__(self) -> None:
        self._embedder = None
        self._reranker = None

    def embed(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
        if self._embedder is None:
            from model_server.embedder import Embedder

            log.info("loading bge-base embedder...")
            self._embedder = Embedder()
        return self._embedder.encode(texts, mode=mode)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        if self._reranker is None:
            from model_server.reranker import Reranker

            log.info("loading bge-reranker-base...")
            self._reranker = Reranker()
        return self._reranker.score(query, passages)


def hit_at_k(retrieved_ids: list[str], gt_ids: set[str], k: int) -> int:
    return 1 if any(rid in gt_ids for rid in retrieved_ids[:k]) else 0


def reciprocal_rank(retrieved_ids: list[str], gt_ids: set[str], k: int) -> float:
    for rank, rid in enumerate(retrieved_ids[:k], start=1):
        if rid in gt_ids:
            return 1.0 / rank
    return 0.0


def _expand_gt_with_parents(gt_ids: set[str], parent_of: dict[str, str | None]) -> set[str]:
    """If a GT chunk has a parent, accept the parent ID as a hit too — the
    `full` stack returns parents after expansion, so we count parent matches
    as semantically equivalent.
    """
    expanded = set(gt_ids)
    for cid in list(gt_ids):
        pid = parent_of.get(cid)
        if pid:
            expanded.add(pid)
    return expanded


async def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=Path("evals/rag/golden.jsonl"), type=Path)
    parser.add_argument("--out", default=None, type=Path)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--stack", choices=STACKS, default="full")
    args = parser.parse_args()

    if not args.golden.exists():
        log.error("missing golden file: %s", args.golden)
        return 1
    out_path = args.out or Path(f"reports/rag_eval_{args.stack}.json")

    golden = [
        json.loads(line)
        for line in args.golden.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    log.info("loaded %d golden records  stack=%s", len(golden), args.stack)

    from app.infra.db import acquire
    from app.services.rag import RAGService

    # Pre-compute the parent-id map so we can score the `full` stack correctly
    # (parents replace children in returned results after parent expansion).
    all_gt = {cid for r in golden for cid in (r.get("ground_truth_chunk_ids") or [])}
    parent_of: dict[str, str | None] = {}
    if all_gt:
        async with acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, parent_id FROM chunks WHERE id = ANY($1)", list(all_gt)
            )
        parent_of = {r["id"]: r["parent_id"] for r in rows}

    rag = RAGService(model_server=_LocalModelServer())
    per_question = []
    t0 = time.time()
    for i, rec in enumerate(golden, 1):
        q = rec["question"]
        gt = set(rec.get("ground_truth_chunk_ids") or [])
        if not gt:
            log.warning("skipping (no GT): %s", q[:60])
            continue
        gt_with_parents = _expand_gt_with_parents(gt, parent_of)
        hits = await rag.retrieve(q, top_k=args.top_k, stack=args.stack)
        retrieved = [h.id for h in hits]
        h5 = hit_at_k(retrieved, gt_with_parents, 5)
        rr10 = reciprocal_rank(retrieved, gt_with_parents, 10)
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    log.info("=" * 60)
    log.info("stack:    %s", args.stack)
    log.info("n:        %d", len(per_question))
    log.info("hit@5:    %.3f", hit5)
    log.info("MRR@10:   %.3f", mrr10)
    for tag, m in by_tag.items():
        log.info("  %s (n=%d)  hit@5=%.3f  MRR@10=%.3f", tag, m["n"], m["hit_at_5"], m["mrr_at_10"])
    log.info("elapsed:  %.1fs", elapsed)
    log.info("report:   %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
