"""BAAI/bge-base-en-v1.5 embedder.

Asymmetric retrieval: queries get the bge instruction prefix, passages do not.
Returns L2-normalized 768-d vectors so cosine = dot product downstream.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Literal

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-base-en-v1.5"
EMBED_DIM = 768
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

Mode = Literal["query", "passage"]


class Embedder:
    """Wraps sentence-transformers BGE-base. Lazy-loaded on first encode."""

    def __init__(self, device: str | None = None) -> None:
        from pathlib import Path

        from sentence_transformers import SentenceTransformer

        self._device = device or os.environ.get("MC_EMBEDDER_DEVICE", "cpu")
        cache_dir = os.environ.get("MC_MODEL_CACHE", str(Path.home() / ".cache" / "mc-models"))
        self._model = SentenceTransformer(MODEL_NAME, device=self._device, cache_folder=cache_dir)
        self._model.max_seq_length = 512
        logger.info("embedder ready: %s on %s", MODEL_NAME, self._device)

    def encode(self, texts: Iterable[str], mode: Mode = "passage") -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        if mode == "query":
            items = [QUERY_PREFIX + t for t in items]
        vecs = self._model.encode(
            items,
            normalize_embeddings=True,
            convert_to_numpy=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return vecs.tolist()
