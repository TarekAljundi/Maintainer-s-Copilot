"""bge-base vs bge-small single-axis embedder ablation.

Pulls existing chunk texts + IDs from Postgres, re-embeds them with
`BAAI/bge-small-en-v1.5` (384-d) in memory, embeds the 25 golden queries with
the same model, and computes hit@5 + MRR@10 on the naive (dense-only) stack.

Compares against the slice-06 baseline reported in reports/rag_eval_naive.json.

Usage (with compose db on localhost:5432):
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python scripts/ablate_embedder.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ablate")

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
GOLDEN_PATH = Path("evals/rag/golden.jsonl")
REPORT_PATH = Path("reports/embedder_ablation.json")


async def run() -> int:
    if not GOLDEN_PATH.exists():
        log.error("missing golden: %s", GOLDEN_PATH)
        return 1

    from sentence_transformers import SentenceTransformer

    from app.infra.db import acquire

    golden = [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    golden = [g for g in golden if g.get("ground_truth_chunk_ids")]
    log.info("loaded %d golden records", len(golden))

    log.info("pulling chunk texts + parent map from db (chunk_seq>=0 only)...")
    async with acquire() as conn:
        rows = await conn.fetch("SELECT id, parent_id, text FROM chunks WHERE chunk_seq >= 0")
    ids = [r["id"] for r in rows]
    texts = [r["text"] for r in rows]
    parent_of = {r["id"]: r["parent_id"] for r in rows}
    log.info("%d child/standalone chunks to re-embed", len(texts))

    cache_dir = os.environ.get("MC_MODEL_CACHE", str(Path.home() / ".cache" / "mc-models"))
    log.info("loading %s...", MODEL_NAME)
    model = SentenceTransformer(MODEL_NAME, device="cpu", cache_folder=cache_dir)
    model.max_seq_length = 512

    t0 = time.time()
    passage_vecs = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        batch_size=32,
        show_progress_bar=False,
    )
    log.info("passage embedding done in %.1fs (shape=%s)", time.time() - t0, passage_vecs.shape)

    queries = [g["question"] for g in golden]
    q_texts = [QUERY_PREFIX + q for q in queries]
    q_vecs = model.encode(
        q_texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        batch_size=32,
        show_progress_bar=False,
    )

    # Cosine = dot product on normalized vectors.
    sims = q_vecs @ passage_vecs.T  # (Q, P)
    hits5 = []
    rrs10 = []
    for i, g in enumerate(golden):
        gt = set(g["ground_truth_chunk_ids"])
        gt_with_parents = set(gt)
        for cid in gt:
            pid = parent_of.get(cid)
            if pid:
                gt_with_parents.add(pid)
        top_idx = np.argsort(-sims[i])[:10]
        retrieved = [ids[j] for j in top_idx]
        h5 = 1 if any(rid in gt_with_parents for rid in retrieved[:5]) else 0
        rr = 0.0
        for rank, rid in enumerate(retrieved, start=1):
            if rid in gt_with_parents:
                rr = 1.0 / rank
                break
        hits5.append(h5)
        rrs10.append(rr)

    hit5 = statistics.mean(hits5)
    mrr10 = statistics.mean(rrs10)
    log.info("=" * 50)
    log.info("model:    %s (384-d)", MODEL_NAME)
    log.info("n:        %d", len(golden))
    log.info("hit@5:    %.3f", hit5)
    log.info("MRR@10:   %.3f", mrr10)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "model": MODEL_NAME,
                "dim": int(passage_vecs.shape[1]),
                "n": len(golden),
                "hit_at_5": hit5,
                "mrr_at_10": mrr10,
                "stack": "naive (dense only)",
            },
            indent=2,
        )
    )
    log.info("report: %s", REPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
