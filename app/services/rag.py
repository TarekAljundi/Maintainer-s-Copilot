"""RAGService — full advanced-RAG pipeline (slice 07).

Pipeline (configurable via `stack`):
  HyDE → dense (HyDE'd embedding) + sparse (original-query FTS)
       → post-filter w/ over-fetch (top-200 each side)
       → weighted RRF (k=60, tuned weights) → top-20
       → cross-encoder rerank (bge-reranker-base) → top-5
       → parent-document expansion
       → list[RetrievedChunk]

Stacks (used by evals/rag/run.py to produce the cumulative-add table):
  - "naive"          : dense top-k (slice-06 floor)
  - "hybrid"         : dense + sparse fused via RRF, no rerank, no HyDE, no parent
  - "hybrid_rerank"  : + reranker
  - "full"           : + HyDE + parent expansion (production stack)

Tuned RRF weights are written by scripts/tune_rrf_weights.py to DECISIONS.md;
the constant below is updated to match the locked winner.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Literal

from app.domain.chunks import RetrievalFilters, RetrievedChunk
from app.infra.model_server_client import ModelServerClient
from app.repositories import chunks as chunks_repo
from app.services import hyde as hyde_svc

log = logging.getLogger(__name__)

Stack = Literal["naive", "hybrid", "hybrid_rerank", "full"]

# Tuned by scripts/tune_rrf_weights.py on the 25-Q golden by MRR@10.
# Winner of the {(1.0,0.5),(1.0,1.0),(1.0,1.5),(0.5,1.0)} grid: (0.5, 1.0)
# — sparse side wins because the golden questions share lexical surface
# with both docs sections and maintainer-answer comments. See DECISIONS.md.
RRF_K = 60
RRF_W_DENSE = 0.5
RRF_W_SPARSE = 1.0

OVERFETCH_PER_SIDE = 200
FUSE_TO = 20
RERANK_TO = 5


def _rrf_fuse(
    dense: list[RetrievedChunk],
    sparse: list[RetrievedChunk],
    w_d: float = RRF_W_DENSE,
    w_s: float = RRF_W_SPARSE,
    k: int = RRF_K,
) -> list[RetrievedChunk]:
    """Weighted RRF: score(doc) = w_d / (k + rank_dense) + w_s / (k + rank_sparse).

    Missing-side rank → 0 contribution from that side.
    """
    by_id: dict[str, RetrievedChunk] = {}
    scores: dict[str, float] = {}
    for rank, c in enumerate(dense, start=1):
        scores[c.id] = scores.get(c.id, 0.0) + w_d / (k + rank)
        by_id[c.id] = c
    for rank, c in enumerate(sparse, start=1):
        scores[c.id] = scores.get(c.id, 0.0) + w_s / (k + rank)
        # Prefer the dense version if both have the same id (more useful score field).
        by_id.setdefault(c.id, c)

    fused: list[RetrievedChunk] = []
    for cid, s in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        c = by_id[cid]
        fused.append(
            RetrievedChunk(
                id=c.id,
                content_type=c.content_type,
                source_id=c.source_id,
                text=c.text,
                score=s,
                breadcrumb=c.breadcrumb,
                section_path=c.section_path,
                labels=c.labels,
                is_answer=c.is_answer,
            )
        )
    return fused


def _dedupe_to_parent(
    fused: Iterable[RetrievedChunk], child_to_parent: dict[str, RetrievedChunk]
) -> list[RetrievedChunk]:
    """Collapse children that share a parent: keep the first occurrence by
    fused score, swap its text for the parent's, drop subsequent siblings.
    """
    out: list[RetrievedChunk] = []
    seen_parents: set[str] = set()
    for c in fused:
        parent = child_to_parent.get(c.id, c)
        if parent.id in seen_parents:
            continue
        seen_parents.add(parent.id)
        out.append(
            RetrievedChunk(
                id=parent.id,
                content_type=parent.content_type,
                source_id=parent.source_id,
                text=parent.text,
                score=c.score,
                breadcrumb=parent.breadcrumb,
                section_path=parent.section_path,
                labels=parent.labels,
                is_answer=parent.is_answer,
            )
        )
    return out


class RAGService:
    def __init__(self, model_server: ModelServerClient | None = None) -> None:
        self._ms = model_server or ModelServerClient()

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: RetrievalFilters | None = None,
        stack: Stack = "full",
        w_d: float = RRF_W_DENSE,
        w_s: float = RRF_W_SPARSE,
    ) -> list[RetrievedChunk]:
        if stack == "naive":
            embeddings = self._ms.embed([query], mode="query")
            return await chunks_repo.dense_search(embeddings[0], top_k=top_k, filters=filters)

        # HyDE on the dense side only (in the "full" stack).
        if stack == "full":
            try:
                dense_query_text = hyde_svc.generate(query)
            except Exception as exc:
                log.warning("HyDE failed, falling back to original query: %s", exc)
                dense_query_text = query
        else:
            dense_query_text = query

        dense_emb = self._ms.embed([dense_query_text], mode="query")[0]

        dense_task = chunks_repo.dense_search(dense_emb, top_k=OVERFETCH_PER_SIDE, filters=filters)
        sparse_task = chunks_repo.fts_search(query, top_k=OVERFETCH_PER_SIDE, filters=filters)
        dense, sparse = await asyncio.gather(dense_task, sparse_task)

        fused = _rrf_fuse(dense, sparse, w_d=w_d, w_s=w_s, k=RRF_K)[:FUSE_TO]

        if stack == "hybrid":
            return fused[:top_k]

        # Rerank with bge-reranker-base cross-encoder.
        passages = [c.text for c in fused]
        scores = self._ms.rerank(query, passages)
        ranked = sorted(zip(fused, scores), key=lambda kv: kv[1], reverse=True)
        reranked = [
            RetrievedChunk(
                id=c.id,
                content_type=c.content_type,
                source_id=c.source_id,
                text=c.text,
                score=float(s),
                breadcrumb=c.breadcrumb,
                section_path=c.section_path,
                labels=c.labels,
                is_answer=c.is_answer,
            )
            for c, s in ranked
        ][:RERANK_TO]

        if stack == "hybrid_rerank":
            return reranked[:top_k]

        # Parent-document expansion: swap each reranked child for its parent
        # (or keep it if it has no parent), dedupe siblings sharing a parent.
        child_to_parent = await chunks_repo.parent_lookup([c.id for c in reranked])
        expanded = _dedupe_to_parent(reranked, child_to_parent)
        return expanded[:top_k]
