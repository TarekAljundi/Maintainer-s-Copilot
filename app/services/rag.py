"""RAGService — pure dense top-k retrieval (slice 06).

Interface:
    retrieve(query, top_k=5, filters=None) -> list[RetrievedChunk]

Slice 06: embed query via bge -> pgvector cosine top-k. Filters arg is
accepted but ignored; HyDE / hybrid / rerank / parent-doc / actual filter
application all land in slice 07.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.chunks import RetrievedChunk
from app.infra.model_server_client import ModelServerClient
from app.repositories import chunks as chunks_repo


@dataclass
class RetrievalFilters:
    content_types: list[str] | None = None
    labels: list[str] | None = None
    is_answer: bool | None = None
    breadcrumb_prefix: str | None = None
    min_closed_at: str | None = None


class RAGService:
    def __init__(self, model_server: ModelServerClient | None = None) -> None:
        self._ms = model_server or ModelServerClient()

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: RetrievalFilters | None = None,
    ) -> list[RetrievedChunk]:
        embeddings = self._ms.embed([query], mode="query")
        return await chunks_repo.dense_search(embeddings[0], top_k=top_k)
