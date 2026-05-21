"""On green main: copy `runs/<sha>.json` -> `main/latest.json` + `main/sha=<sha>.json`.

`main/latest.json` is the source of truth that `evals.compare` diffs against
on the next build. The sha-keyed copy gives us a recoverable history if a bad
baseline ever lands.

Usage:
    uv run python -m evals.promote --sha $GITHUB_SHA
"""

from __future__ import annotations

import argparse
import sys


def run(sha: str) -> int:
    from app.infra.minio import MinIOClient

    client = MinIOClient()
    src = f"runs/{sha}.json"
    bucket = client.default_bucket
    client.copy_object(src, "main/latest.json")
    client.copy_object(src, f"main/sha={sha}.json")
    print(
        f"promoted s3://{bucket}/{src} -> main/latest.json, main/sha={sha}.json",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sha", required=True)
    args = p.parse_args()
    return run(args.sha)


if __name__ == "__main__":
    sys.exit(main())
