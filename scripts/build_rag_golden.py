"""Golden-set builder for the 25-Q RAG eval.

Two modes:
  --mode interactive   prompts for each draft (manual GT selection)
  --mode auto          (default) picks GT chunk_ids deterministically from
                       the draft's gt_filter hints by ranking candidates by
                       dense similarity to the question.

Usage:
    POSTGRES_HOST=localhost POSTGRES_PORT_INTERNAL=5432 \\
        python scripts/build_rag_golden.py --from evals/rag/drafts.jsonl \\
            --out evals/rag/golden.jsonl

Drafts JSONL shape (one per line):
    {
      "question": "...",
      "ideal_answer": "...",
      "tags": ["docs" | "issue", ...],
      "gt_source_id": "doc/source/user_guide/io.rst" | "65190",
      "gt_breadcrumb_contains": "NA",          # optional, docs only
      "gt_is_answer": true                     # optional, issues only
    }

Idempotent: questions already present in --out are skipped on subsequent runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_golden")


def load_existing(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    seen: set[str] = set()
    for ln in out_path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        seen.add(json.loads(ln)["question"])
    return seen


def fmt_hit(idx: int, hit) -> str:
    preview = hit.text.replace("\n", " ")[:200]
    cite = hit.citation()
    return f"  [{idx:>2}] {hit.id}  {hit.content_type:>5}  {cite}\n       {preview}"


async def _resolve_auto(d: dict, top_n: int) -> list[str]:
    """Pick GT chunk_ids by ranking gt_filter candidates with Postgres FTS against the question.

    Uses the chunks table's generated tsvector column (no embedding round-trip),
    so the resolver can run with only Postgres up.
    """
    from app.infra.db import acquire

    where_parts: list[str] = []
    params: list = []
    is_issue = "issue" in (d.get("tags") or [])

    where_parts.append(f"content_type = ${len(params) + 1}")
    params.append("issue" if is_issue else "docs")

    if d.get("gt_source_id"):
        where_parts.append(f"source_id = ${len(params) + 1}")
        params.append(d["gt_source_id"])
    if d.get("gt_breadcrumb_contains"):
        where_parts.append(f"breadcrumb ILIKE ${len(params) + 1}")
        params.append(f"%{d['gt_breadcrumb_contains']}%")
    if d.get("gt_is_answer") is True:
        where_parts.append("is_answer = TRUE")

    qpos = len(params) + 1
    params.append(d["question"])
    lim_pos = len(params) + 1
    params.append(top_n)

    sql = f"""
        SELECT id, ts_rank_cd(tsv, plainto_tsquery('english', ${qpos})) AS score
        FROM chunks
        WHERE {' AND '.join(where_parts)}
        ORDER BY score DESC, chunk_seq ASC
        LIMIT ${lim_pos}
    """
    async with acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [r["id"] for r in rows]


async def _resolve_interactive(d: dict, top_k: int) -> list[str]:
    from app.services.rag import RAGService

    q = d["question"]
    print("\n" + "=" * 70)
    print(f"Q: {q}")
    print(f"ideal: {d.get('ideal_answer', '')[:200]}")
    print("-" * 70)
    rag = RAGService()
    hits = await rag.retrieve(q, top_k=top_k)
    for i, h in enumerate(hits, 1):
        print(fmt_hit(i, h))
    print()
    choice = input(
        "Enter GT chunk_ids space-separated (or hit numbers like '1 3 5'), or 's' to skip: "
    ).strip()
    if choice.lower() in ("s", "skip", ""):
        return []
    gt_ids: list[str] = []
    for tok in choice.split():
        if tok.isdigit() and 1 <= int(tok) <= len(hits):
            gt_ids.append(hits[int(tok) - 1].id)
        else:
            gt_ids.append(tok)
    return gt_ids


async def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="drafts", required=True, type=Path)
    parser.add_argument("--out", default=Path("evals/rag/golden.jsonl"), type=Path)
    parser.add_argument("--mode", choices=["auto", "interactive"], default="auto")
    parser.add_argument("--top-n", type=int, default=3, help="GT chunks per draft (auto mode)")
    parser.add_argument("--top-k", type=int, default=20, help="candidates shown (interactive)")
    args = parser.parse_args()

    if not args.drafts.exists():
        log.error("missing drafts file: %s", args.drafts)
        return 1

    drafts = [
        json.loads(line)
        for line in args.drafts.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    log.warning("loaded %d drafts (mode=%s)", len(drafts), args.mode)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    seen = load_existing(args.out)

    appended = 0
    for d in drafts:
        q = d["question"]
        if q in seen:
            print(f"-- skipping (already in {args.out}): {q[:80]}")
            continue

        if args.mode == "auto":
            gt_ids = await _resolve_auto(d, args.top_n)
            if not gt_ids:
                print(f"!! no GT candidates matched for: {q[:80]}")
                continue
            print(f"   {q[:80]}  ->  {gt_ids}")
        else:
            gt_ids = await _resolve_interactive(d, args.top_k)
            if not gt_ids:
                continue

        record = {
            "question": q,
            "ideal_answer": d.get("ideal_answer", ""),
            "ground_truth_chunk_ids": gt_ids,
            "tags": d.get("tags", []),
        }
        with args.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        appended += 1

    print(f"\nDone. Appended {appended} records. Total in {args.out}: {len(seen) + appended}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
