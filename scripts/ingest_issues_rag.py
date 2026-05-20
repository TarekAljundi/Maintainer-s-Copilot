"""Ingest the RAG-holdout slice of pandas issues into the chunks table.

Reads data/splits/rag_holdout.jsonl (260 records, see DECISIONS.md §Dataset),
chunks per-comment via app.services.chunking.chunk_issue, embeds via bge-base,
inserts via app.repositories.chunks.insert_many.

Usage (from repo root, with the compose db running on localhost:5432):
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python scripts/ingest_issues_rag.py [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

RAG_HOLDOUT = Path("data/splits/rag_holdout.jsonl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_issues_rag")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch", type=int, default=64)
    args = parser.parse_args()

    if not RAG_HOLDOUT.exists():
        log.error("missing %s — run scripts/pull_dataset.py first", RAG_HOLDOUT)
        return 1

    from app.repositories import chunks as chunks_repo
    from app.services.chunking import chunk_issue
    from model_server.embedder import Embedder

    records = []
    with RAG_HOLDOUT.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    if args.limit:
        records = records[: args.limit]
    log.info("loaded %d rag_holdout records", len(records))

    all_chunks = []
    for r in records:
        all_chunks.extend(chunk_issue(r))
    log.info("produced %d issue chunks", len(all_chunks))
    if not all_chunks:
        return 1

    log.info("loading embedder (bge-base)...")
    embedder = Embedder()
    t0 = time.time()
    for i in range(0, len(all_chunks), args.batch):
        batch = all_chunks[i : i + args.batch]
        vecs = embedder.encode([c.text for c in batch], mode="passage")
        for c, v in zip(batch, vecs):
            c.embedding = v
        if (i // args.batch) % 10 == 0:
            log.info("embedded %d/%d (%.1fs)", i + len(batch), len(all_chunks), time.time() - t0)
    log.info("embedding done in %.1fs", time.time() - t0)

    inserted = await chunks_repo.insert_many(all_chunks)
    log.info("insert_many returned %d", inserted)
    final = await chunks_repo.count_by_type()
    log.info("chunks table now: %s", final)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
