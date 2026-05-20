"""HTTP client to model-server. Maps failures -> ToolFailure subclasses.

Interface:
    classify(text), extract(text), embed(texts, mode='query'|'passage')

Slice 05: classify + extract + health. Slice 06: embed.
rerank lands with slice 07; summarize() is NOT a model-server hop — it lives
in app/services/summarizer.py.
"""

from __future__ import annotations

import os

import httpx

from app.domain.exceptions import ClassifierUnavailable, NERFailure, RAGRetrievalFailure


class ModelServerClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self._base = (
            base_url or os.environ.get("MODEL_SERVER_URL", "http://model-server:8001")
        ).rstrip("/")
        self._timeout = timeout

    def health(self) -> dict:
        try:
            r = httpx.get(f"{self._base}/health", timeout=self._timeout)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.RequestError) as exc:
            raise ClassifierUnavailable(f"model-server /health failed: {exc}") from exc
        return r.json()

    def classify(self, text: str, title: str | None = None) -> dict:
        payload: dict = {"text": text}
        if title is not None:
            payload["title"] = title
        # Two-try retry: one transient retry is cheap and covers a model-server
        # restart mid-turn. Full backoff/circuit-breaker lands in slice 09.
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                r = httpx.post(f"{self._base}/classify", json=payload, timeout=self._timeout)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.RequestError) as exc:
                last_exc = exc
        raise ClassifierUnavailable(f"model-server /classify failed: {last_exc}") from last_exc

    def extract(self, text: str) -> dict:
        """POST /extract -> {entities: [...]}. Same 2-try retry as classify()."""
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                r = httpx.post(f"{self._base}/extract", json={"text": text}, timeout=self._timeout)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.RequestError) as exc:
                last_exc = exc
        raise NERFailure(f"model-server /extract failed: {last_exc}") from last_exc

    def embed(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
        """POST /embed -> embeddings list. Maps failures to RAGRetrievalFailure."""
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                r = httpx.post(
                    f"{self._base}/embed",
                    json={"texts": texts, "mode": mode},
                    timeout=self._timeout,
                )
                r.raise_for_status()
                return r.json()["embeddings"]
            except (httpx.HTTPError, httpx.RequestError) as exc:
                last_exc = exc
        raise RAGRetrievalFailure(f"model-server /embed failed: {last_exc}") from last_exc
