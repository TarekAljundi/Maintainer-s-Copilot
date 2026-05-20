"""BAAI/bge-reranker-base — cross-encoder scoring (query, passage) pairs.

Returns raw cross-encoder logits; the RAG pipeline only needs the *order*,
not normalized probabilities. Higher score = more relevant.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-reranker-base"


class Reranker:
    """Wraps sentence-transformers CrossEncoder. Lazy-loaded on first call."""

    def __init__(self, device: str | None = None) -> None:
        from sentence_transformers import CrossEncoder

        self._device = device or os.environ.get("MC_RERANKER_DEVICE", "cpu")
        cache_dir = os.environ.get("MC_MODEL_CACHE", str(Path.home() / ".cache" / "mc-models"))
        self._model = CrossEncoder(MODEL_NAME, device=self._device, cache_folder=cache_dir)
        logger.info("reranker ready: %s on %s", MODEL_NAME, self._device)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._model.predict(pairs, batch_size=16, show_progress_bar=False)
        return [float(s) for s in scores]
