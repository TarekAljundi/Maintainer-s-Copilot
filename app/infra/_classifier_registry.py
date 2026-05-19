"""Pinned classifier metadata. Rewritten by scripts/train_classifier.py after each run.

The boot check (#5) compares the SHA below against the value returned by model-server's
/health endpoint; mismatch -> SystemExit(1).

Until the first training run lands, WEIGHTS_SHA256 is the empty string, which the boot
check treats as "unpinned" and allows. CI rejects empty-pin on main.
"""

from __future__ import annotations

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")

WEIGHTS_SHA256: str = (
    "1966ceac02bef9682998894fe01ba0536f6068700e0fc72ace268f967a423df1"  # set by train_classifier.py
)
WEIGHTS_BUCKET: str = "mc-models"
WEIGHTS_PREFIX: str = "classifier/v1"
