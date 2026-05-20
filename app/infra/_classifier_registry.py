"""Pinned classifier metadata. Rewritten by scripts/train_classifier.py after each run.

The boot check (#5) compares the SHA below against the value returned by model-server's
/health endpoint; mismatch -> SystemExit(1).

Until the first training run lands, WEIGHTS_SHA256 is the empty string, which the boot
check treats as "unpinned" and allows. CI rejects empty-pin on main.
"""

from __future__ import annotations

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")

# Set by scripts/train_classifier.py:rewrite_registry().
WEIGHTS_SHA256: str = "efb5388e886552d6c35ac861b1b8bcf6e28b91b168346957f3c646bbe88ba21e"
WEIGHTS_BUCKET: str = "mc-models"
WEIGHTS_PREFIX: str = "classifier/v1"
