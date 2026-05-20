"""Pull pandas-dev/pandas closed issues -> time-stratified splits -> MinIO.

Usage (from repo root, with compose stack up):
    MC_BLOB_FROM_HOST=1 GITHUB_TOKEN=ghp_... VAULT_TOKEN=dev-root-token \
        VAULT_ADDR=http://localhost:8200 python scripts/pull_dataset.py

Outputs:
    data/raw/issues_page_{N}.json         GitHub API page caches (resumable)
    data/raw/comments_{N}.json            per-issue comment caches (RAG candidates only)
    data/splits/{train,val,test,rag_holdout,unlabeled}.jsonl
    data/splits/manifest.json             SHA-256s + git SHA + counts

Then uploads splits/* to s3://mc-evals/datasets/pandas-issues/v1/.

AC: PRD §Dataset and labels (Q1-Q5). Corpus swapped from fastapi/fastapi to
pandas-dev/pandas — see DECISIONS.md §Dataset for the rationale.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import httpx

REPO = "pandas-dev/pandas"
CREATED_FLOOR = "2020-01-01T00:00:00Z"
LABEL_PRIORITY = ("bug", "feature", "docs", "question")

# Map raw GitHub label name (lowercased) -> canonical class. Labels not in the
# map are ignored for classification (they may be workflow/component labels
# like "Needs Discussion", "Performance", "IO", etc.).
LABEL_MAP: dict[str, str] = {
    "bug": "bug",
    "enhancement": "feature",
    "docs": "docs",
    "documentation": "docs",
    "usage question": "question",
}

MAINTAINER_ASSOC = {"OWNER", "MEMBER", "COLLABORATOR"}

RAW_DIR = Path("data/raw")
SPLITS_DIR = Path("data/splits")
MINIO_PREFIX = "datasets/pandas-issues/v1"


# ---------- pure logic (unit-tested) ----------


def resolve_label(label_names: Iterable[str]) -> str | None:
    """Map raw GitHub labels to canonical class; tie-break by LABEL_PRIORITY.

    A label survives only if it appears in LABEL_MAP — everything else
    (workflow, component, subcomponent) is ignored. Returns None when no
    canonical class is found, i.e. the issue is "unlabeled" for our taxonomy.
    """
    names = {n.lower() for n in label_names}
    mapped = {LABEL_MAP[n] for n in names if n in LABEL_MAP}
    for canonical in LABEL_PRIORITY:
        if canonical in mapped:
            return canonical
    return None


def is_eligible(issue: dict) -> bool:
    """Filter rule: closed, not 'not planned', created >= floor, not a PR."""
    if "pull_request" in issue:
        return False
    if issue.get("state") != "closed":
        return False
    if issue.get("state_reason") == "not_planned":
        return False
    if (issue.get("created_at") or "") < CREATED_FLOOR:
        return False
    return True


def time_split(records: list[dict]) -> dict[str, list[dict]]:
    """Sort by closed_at asc, slice 70/10/15/5. Train = oldest, RAG = newest."""
    ordered = sorted(records, key=lambda r: r["closed_at"])
    n = len(ordered)
    i_train = int(n * 0.70)
    i_val = i_train + int(n * 0.10)
    i_test = i_val + int(n * 0.15)
    return {
        "train": ordered[:i_train],
        "val": ordered[i_train:i_val],
        "test": ordered[i_val:i_test],
        "rag_candidates": ordered[i_test:],  # filter further (answered/maintainer)
    }


def has_maintainer_answer(labels: Iterable[str], comments: list[dict]) -> bool:
    """RAG-holdout filter: issue has at least one maintainer-association comment.

    Note: the fastapi-specific 'answered' workflow label is no longer
    consulted — pandas doesn't use that convention. Maintainer-association
    comment alone is the filter.
    """
    del labels  # kept in signature for call-site stability across corpora
    return any(c.get("author_association") in MAINTAINER_ASSOC for c in (comments or []))


def per_class_counts(records: list[dict]) -> Counter:
    return Counter(r["label"] for r in records)


# ---------- I/O ----------


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if tok := os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = f"Bearer {tok}"
    return h


def _month_windows(start: str, end_inclusive: str) -> list[tuple[str, str]]:
    """Yield (YYYY-MM-01, YYYY-MM-LAST) windows covering [start, end_inclusive].

    `start` and `end_inclusive` are 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SSZ' strings.
    """
    s = date.fromisoformat(start[:10])
    e = date.fromisoformat(end_inclusive[:10])
    windows: list[tuple[str, str]] = []
    y, m = s.year, s.month
    while (y, m) <= (e.year, e.month):
        first = f"{y:04d}-{m:02d}-01"
        # Last day of this month = first day of next month minus one day.
        if m == 12:
            ny, nm = y + 1, 1
        else:
            ny, nm = y, m + 1
        last = (date(ny, nm, 1) - timedelta(days=1)).isoformat()
        # Clamp the final window to end_inclusive.
        if (y, m) == (e.year, e.month):
            last = e.isoformat()
        windows.append((first, last))
        y, m = ny, nm
    return windows


def _search_one_page(client: httpx.Client, q: str, page: int) -> dict:
    """Call GET /search/issues with rate-limit + 422 handling. Returns the JSON body."""
    while True:
        r = client.get(
            "https://api.github.com/search/issues",
            params={"q": q, "per_page": 100, "page": page, "sort": "created", "order": "asc"},
        )
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - int(time.time()), 1)
            print(f"  rate-limited (search), sleeping {wait}s ...", file=sys.stderr)
            time.sleep(wait)
            continue
        # Search API also returns 422 if you go past 1000 results in a single
        # query window. We treat that as "this window is full" — caller may
        # need to subdivide; we just stop the inner pagination.
        if r.status_code == 422:
            return {"items": [], "total_count": -1, "exhausted": True}
        r.raise_for_status()
        body = r.json()
        body["exhausted"] = False
        return body


def fetch_issues(client: httpx.Client) -> list[dict]:
    """Pull pandas closed issues via the Search API, windowed by month.

    The plain /issues endpoint caps pagination at ~1000 records-deep AND mixes
    PRs into the stream, wasting most of our pull on PRs we'll discard. Search
    API filters with `is:issue is:closed` directly and lets us window by
    `created:YYYY-MM-01..YYYY-MM-DD`, so we get every actual issue with no PR
    overhead.

    Tradeoffs:
      - Search API is rate-limited tighter (30 req/min vs 5000/hr) — handled.
      - Each window caps at 1000 results; pandas months are ~150-300 issues
        each, well under the cap. If a window ever returns 1000+ we'd need to
        subdivide; for pandas this doesn't trigger.
      - Caching uses one file per month-window so re-runs skip what's done.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    windows = _month_windows(CREATED_FLOOR, today)

    issues_by_number: dict[int, dict] = {}
    for w_start, w_end in windows:
        cache_name = f"issues_window_{w_start[:7]}.json"
        cache = RAW_DIR / cache_name
        if cache.exists():
            window_items = json.loads(cache.read_text(encoding="utf-8"))
            print(f"  {w_start[:7]}: cached ({len(window_items)} items)")
        else:
            q = f"repo:{REPO} is:issue is:closed created:{w_start}..{w_end}"
            window_items: list[dict] = []
            for page in range(1, 11):  # 10 pages * 100 = 1000 max per window
                body = _search_one_page(client, q, page)
                if body.get("exhausted"):
                    print(
                        f"  {w_start[:7]} page {page}: 422 — window over 1000 items "
                        f"(would need finer slicing); kept first {len(window_items)}",
                        file=sys.stderr,
                    )
                    break
                items = body.get("items", []) or []
                window_items.extend(items)
                if len(items) < 100:
                    break
            cache.write_text(json.dumps(window_items), encoding="utf-8")
            print(f"  {w_start[:7]}: {len(window_items)} items")
        for it in window_items:
            num = it.get("number")
            if num is not None and num not in issues_by_number:
                issues_by_number[num] = it
    return list(issues_by_number.values())


def fetch_comments(client: httpx.Client, number: int) -> list[dict]:
    cache = RAW_DIR / f"comments_{number}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    url = f"https://api.github.com/repos/{REPO}/issues/{number}/comments"
    r = client.get(url, params={"per_page": 100})
    if r.status_code == 403 and "rate limit" in r.text.lower():
        reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
        time.sleep(max(reset - int(time.time()), 1))
        return fetch_comments(client, number)
    r.raise_for_status()
    data = r.json()
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def to_record(issue: dict, label: str | None, comments: list[dict] | None = None) -> dict:
    """Project an issue to the columns downstream slices consume."""
    return {
        "number": issue["number"],
        "title": issue.get("title") or "",
        "body": issue.get("body") or "",
        "labels": [lbl["name"] for lbl in issue.get("labels", [])],
        "created_at": issue["created_at"],
        "closed_at": issue["closed_at"],
        "state_reason": issue.get("state_reason"),
        "html_url": issue.get("html_url"),
        "label": label,
        "comments": comments,
    }


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


# ---------- orchestration ----------


def build_splits(issues: list[dict], client: httpx.Client) -> dict[str, list[dict]]:
    labeled: list[dict] = []
    unlabeled: list[dict] = []
    seen: set[int] = set()
    for raw in issues:
        if not is_eligible(raw):
            continue
        if raw["number"] in seen:
            continue
        seen.add(raw["number"])
        label_names = [lbl["name"] for lbl in raw.get("labels", [])]
        label = resolve_label(label_names)
        rec = to_record(raw, label)
        (labeled if label else unlabeled).append(rec)

    splits = time_split(labeled)
    # RAG holdout = candidates intersected with (answered label OR maintainer comment)
    rag_holdout: list[dict] = []
    print(f"\nfetching comments for {len(splits['rag_candidates'])} RAG candidates ...")
    for rec in splits["rag_candidates"]:
        comments = fetch_comments(client, rec["number"])
        if has_maintainer_answer(rec["labels"], comments):
            rec_with_comments = {**rec, "comments": comments}
            rag_holdout.append(rec_with_comments)

    return {
        "train": splits["train"],
        "val": splits["val"],
        "test": splits["test"],
        "rag_holdout": rag_holdout,
        "unlabeled": unlabeled,
    }


def report(splits: dict[str, list[dict]]) -> str:
    lines = [
        f"\nDataset v1 ({REPO}, created >= {CREATED_FLOOR})\n",
        f"  total labeled: {sum(len(splits[k]) for k in ('train', 'val', 'test', 'rag_holdout'))}",
        f"  unlabeled: {len(splits['unlabeled'])}",
        "",
        f"{'split':<12} {'n':>6}  per-class (counts)",
        f"{'-' * 12} {'-' * 6}  {'-' * 40}",
    ]
    for split in ("train", "val", "test", "rag_holdout"):
        counts = per_class_counts(splits[split])
        per_class = []
        for cls in LABEL_PRIORITY:
            n = counts.get(cls, 0)
            mark = f"  ({cls}: n={n})" if (split == "test" and n < 5) else f"  {cls}={n}"
            per_class.append(mark)
        lines.append(f"{split:<12} {len(splits[split]):>6}  {''.join(per_class)}")
    return "\n".join(lines)


def write_outputs_and_manifest(splits: dict[str, list[dict]]) -> Path:
    SPLITS_DIR.mkdir(parents=True, exist_ok=True)
    file_hashes: dict[str, str] = {}
    counts: dict[str, int] = {}
    for name, records in splits.items():
        p = SPLITS_DIR / f"{name}.jsonl"
        write_jsonl(p, records)
        file_hashes[f"{name}.jsonl"] = sha256_file(p)
        counts[name] = len(records)
    manifest = {
        "version": "v1",
        "repo": REPO,
        "created_at_floor": CREATED_FLOOR,
        "git_sha": git_sha(),
        "counts": counts,
        "sha256": file_hashes,
    }
    mp = SPLITS_DIR / "manifest.json"
    mp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return mp


def upload(manifest_path: Path) -> None:
    # Lazy import so unit tests don't need vault/minio infra.
    from app.infra.minio import MinIOClient

    client = MinIOClient()
    for fname in [
        "train.jsonl",
        "val.jsonl",
        "test.jsonl",
        "rag_holdout.jsonl",
        "unlabeled.jsonl",
        "manifest.json",
    ]:
        client.put_file(f"{MINIO_PREFIX}/{fname}", SPLITS_DIR / fname)
    print(f"\nuploaded -> s3://{client.default_bucket}/{MINIO_PREFIX}/  ({manifest_path})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true", help="skip MinIO upload")
    args = parser.parse_args()

    if not os.environ.get("GITHUB_TOKEN"):
        print("WARN: no GITHUB_TOKEN — unauthenticated, will rate-limit at ~60/hr", file=sys.stderr)

    with httpx.Client(headers=_gh_headers(), timeout=30.0, follow_redirects=True) as client:
        print(f"fetching {REPO} closed issues since {CREATED_FLOOR} ...")
        raw = fetch_issues(client)
        print(f"\nfetched {len(raw)} raw items")
        splits = build_splits(raw, client)

    print(report(splits))
    mp = write_outputs_and_manifest(splits)
    print(f"\nwrote {mp}")

    if not args.no_upload:
        upload(mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
