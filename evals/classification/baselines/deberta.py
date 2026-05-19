"""DeBERTa adapter: load `data/models/classifier/v1/` directly with transformers.

Keeps the classification eval offline-runnable: no model-server container, no
MinIO round-trip, just the local artifact on disk. CI (slice 15) restores the
artifact from MinIO before invoking the eval, so the same code path serves
both contexts.

Latency is measured per-call (one-at-a-time), matching the LLM and classical
baselines so p50/p99 are apples-to-apples.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

import torch  # type: ignore[import-not-found]
from transformers import (  # type: ignore[import-not-found]
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

from model_server.preprocess import build_input

LABELS: tuple[str, ...] = ("bug", "feature", "docs", "question")
ARTIFACT_DIR = Path("data/models/classifier/v1")
MAX_LEN = 512


class DeBERTaPredictor:
    def __init__(self, artifact_dir: Path = ARTIFACT_DIR) -> None:
        if not (artifact_dir / "model.safetensors").exists():
            raise FileNotFoundError(
                f"missing classifier artifact at {artifact_dir} — run scripts/train_classifier.py"
            )
        self.tokenizer = AutoTokenizer.from_pretrained(str(artifact_dir))
        self.model = AutoModelForSequenceClassification.from_pretrained(str(artifact_dir))
        self.model.eval()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)

    @torch.no_grad()
    def _predict_one(self, text: str) -> str:
        enc = self.tokenizer(
            text,
            truncation=True,
            max_length=MAX_LEN,
            return_tensors="pt",
            padding=False,
        ).to(self.device)
        logits = self.model(**enc).logits
        idx = int(logits.argmax(dim=-1).item())
        return LABELS[idx]


def predict_batch(
    records: Iterable[dict],
    artifact_dir: Path = ARTIFACT_DIR,
) -> tuple[list[str], list[float]]:
    """Predict labels + per-record wall-clock latency in ms."""
    predictor = DeBERTaPredictor(artifact_dir)
    labels_out: list[str] = []
    latencies_ms: list[float] = []
    for rec in records:
        text = build_input(rec.get("title"), rec.get("body"))
        t0 = time.perf_counter()
        labels_out.append(predictor._predict_one(text))
        latencies_ms.append((time.perf_counter() - t0) * 1000)
    return labels_out, latencies_ms
