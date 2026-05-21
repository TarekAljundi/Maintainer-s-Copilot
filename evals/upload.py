"""Upload eval_report.json to MinIO at `s3://mc-evals/runs/<sha>.json`.

Used by CI on every PR + main build so we have a history of every report
we ever ran. `evals.promote` copies the run to `main/latest.json` on green main.

Usage:
    uv run python -m evals.upload --file reports/eval_report.json --sha $GITHUB_SHA
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def run(file_path: Path, sha: str) -> int:
    from app.infra.minio import MinIOClient

    client = MinIOClient()
    client.ensure_bucket()
    key = f"runs/{sha}.json"
    client.put_file(key, file_path)
    print(f"uploaded {file_path} -> s3://{client.default_bucket}/{key}", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", type=Path, required=True)
    p.add_argument("--sha", required=True)
    args = p.parse_args()
    return run(args.file, args.sha)


if __name__ == "__main__":
    sys.exit(main())
