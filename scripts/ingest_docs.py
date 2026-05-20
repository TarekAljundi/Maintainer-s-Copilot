"""Ingest pandas-dev/pandas RST docs into the chunks table.

Strategy:
    1. Shallow-clone pandas-dev/pandas at HEAD (or a passed --pandas-sha) into
       data/raw/pandas-docs/, log the resolved SHA in data/raw/pandas_docs_manifest.json.
    2. Walk doc/source/{getting_started,user_guide,development}/**/*.rst.
       (Skip doc/source/reference/ — autogen API doc, low signal for QA, would
       flood the index. Skip doc/source/whatsnew/ — release-notes churn.)
    3. Chunk each file via app.services.chunking.chunk_docs.
    4. Embed via model_server.embedder.Embedder (local; no HTTP).
    5. Bulk-insert into chunks via app.repositories.chunks.insert_many.

Usage (from repo root, with `docker compose up db` running on localhost:5432):
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python scripts/ingest_docs.py [--pandas-sha <sha>] [--limit-files N]

Idempotent — chunks have stable IDs and ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_URL = "https://github.com/pandas-dev/pandas.git"
DOCS_ROOT_REL = Path("doc/source")
KEEP_PREFIXES = ("getting_started", "user_guide", "development")
CLONE_DIR = Path("data/raw/pandas-docs")
MANIFEST_PATH = Path("data/raw/pandas_docs_manifest.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_docs")


def clone(sha: str | None) -> str:
    """Clone or update the pandas repo. Returns the resolved SHA."""
    if CLONE_DIR.exists():
        log.info("reusing existing clone at %s", CLONE_DIR)
    else:
        CLONE_DIR.parent.mkdir(parents=True, exist_ok=True)
        depth_args = ["--depth", "1"] if sha is None else []
        subprocess.run(["git", "clone", *depth_args, REPO_URL, str(CLONE_DIR)], check=True)
    if sha is not None:
        subprocess.run(
            ["git", "-C", str(CLONE_DIR), "fetch", "--depth", "1", "origin", sha],
            check=True,
        )
        subprocess.run(["git", "-C", str(CLONE_DIR), "checkout", sha], check=True)
    resolved = subprocess.run(
        ["git", "-C", str(CLONE_DIR), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return resolved


def walk_rst(docs_root: Path):
    for prefix in KEEP_PREFIXES:
        base = docs_root / prefix
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.rst")):
            yield path


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pandas-sha", default=None)
    parser.add_argument("--limit-files", type=int, default=None)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="re-clone pandas before ingest (drops local edits)",
    )
    args = parser.parse_args()

    if args.reset and CLONE_DIR.exists():
        shutil.rmtree(CLONE_DIR)

    resolved_sha = clone(args.pandas_sha)
    log.info("pandas SHA: %s", resolved_sha)

    from app.repositories import chunks as chunks_repo
    from app.services.chunking import chunk_docs
    from model_server.embedder import Embedder

    docs_root = CLONE_DIR / DOCS_ROOT_REL
    files = list(walk_rst(docs_root))
    if args.limit_files:
        files = files[: args.limit_files]
    log.info("found %d RST files under %s", len(files), docs_root)

    # Build chunks in memory first (fast), then embed in big batches.
    all_chunks = []
    for path in files:
        rel = path.relative_to(CLONE_DIR).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            log.warning("skipping non-utf8 file %s", rel)
            continue
        chunks = chunk_docs(text, source_id=rel)
        all_chunks.extend(chunks)
    log.info("produced %d docs chunks", len(all_chunks))

    if not all_chunks:
        log.error("no chunks — aborting")
        return 1

    log.info("loading embedder (bge-base, this can take a minute on first run)...")
    embedder = Embedder()
    t0 = time.time()
    for i in range(0, len(all_chunks), args.batch):
        batch = all_chunks[i : i + args.batch]
        vecs = embedder.encode([c.text for c in batch], mode="passage")
        for c, v in zip(batch, vecs):
            c.embedding = v
        if (i // args.batch) % 10 == 0:
            log.info(
                "embedded %d/%d (%.1fs elapsed)", i + len(batch), len(all_chunks), time.time() - t0
            )
    log.info("embedding done in %.1fs", time.time() - t0)

    log.info("inserting into chunks...")
    inserted = await chunks_repo.insert_many(all_chunks)
    log.info("insert_many returned %d", inserted)

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "pandas_sha": resolved_sha,
                "files_scanned": len(files),
                "chunks_produced": len(all_chunks),
                "kept_prefixes": list(KEEP_PREFIXES),
                "embedder": "BAAI/bge-base-en-v1.5",
            },
            indent=2,
        )
    )
    log.info("wrote %s", MANIFEST_PATH)

    final = await chunks_repo.count_by_type()
    log.info("chunks table now: %s", final)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
