"""Pinned classifier metadata. Rewritten by scripts/train_classifier.py after each run.

The boot check (#5) compares the SHA below against the value returned by model-server's
/health endpoint; mismatch -> SystemExit(1).

Until the first training run lands, WEIGHTS_SHA256 is the empty string, which the boot
check treats as "unpinned" and allows. CI rejects empty-pin on main.
"""

from __future__ import annotations

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")

# Set by scripts/train_classifier.py:rewrite_registry().
WEIGHTS_SHA256: str = "f4a5c67f8e72119a97270bb7a93a3657bcf9ee3db73cbe4f74123636261a0975"
WEIGHTS_BUCKET: str = "mc-models"
WEIGHTS_PREFIX: str = "classifier/v1"
