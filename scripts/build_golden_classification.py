"""Sample candidates for the classification golden set.

Sources from `data/splits/rag_holdout.jsonl` only. That slice is:
  - the newest 5% intersected with maintainer-answered,
  - never used to train or hyperparam-tune the classifier,
  - the only split that carries any `docs` records (n=4) at all.

Using it for both the classification golden set and the RAG golden set is
fine — different schemas, different evaluation objectives.

Output: `evals/classification/golden.jsonl` with one record per line:
    {id, title, body, label, source, html_url, notes}

The `label` is taken from the source split's tiebreak-resolved label.
The user reviews each record and may flip / drop / replace before commit.

Run once, deterministically (seed=42):
    uv run python scripts/build_golden_classification.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

SOURCE = Path("data/splits/rag_holdout.jsonl")
OUT = Path("evals/classification/golden.jsonl")
TARGET: dict[str, int] = {"bug": 7, "feature": 7, "docs": 4, "question": 7}  # = 25
SEED = 42


def load_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def sample(records: list[dict], target: dict[str, int], seed: int) -> list[dict]:
    rng = random.Random(seed)
    by_label: dict[str, list[dict]] = {label: [] for label in target}
    for r in records:
        label = r.get("label")
        if label in by_label:
            by_label[label].append(r)

    picked: list[dict] = []
    for label, n in target.items():
        pool = by_label[label]
        if len(pool) < n:
            print(
                f"WARNING: label {label!r} has only {len(pool)} records in {SOURCE.name} "
                f"(target was {n}); taking all of them",
                file=sys.stderr,
            )
            chosen = pool
        else:
            chosen = rng.sample(pool, n)
        picked.extend(chosen)
    return picked


def to_golden_row(rec: dict, idx: int) -> dict:
    return {
        "id": f"gold-{idx:03d}",
        "title": rec.get("title", ""),
        "body": rec.get("body", ""),
        "label": rec["label"],
        "source": f"rag_holdout#{rec.get('number')}",
        "html_url": rec.get("html_url", ""),
        "notes": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing golden.jsonl (default refuses if non-empty)",
    )
    args = parser.parse_args()

    if args.out.exists() and args.out.stat().st_size > 0 and not args.force:
        print(
            f"refusing to overwrite non-empty {args.out} without --force",
            file=sys.stderr,
        )
        return 2

    records = load_jsonl(SOURCE)
    picked = sample(records, TARGET, args.seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for i, rec in enumerate(picked):
            row = to_golden_row(rec, i)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts: dict[str, int] = {}
    for row in picked:
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    print(f"wrote {len(picked)} records -> {args.out}")
    print(f"per-class: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
