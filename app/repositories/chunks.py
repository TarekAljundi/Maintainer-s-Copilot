"""SQL for chunks: bulk insert, dense + sparse retrieval, parent lookup.

Slice 06: insert + naive dense top-k.
Slice 07: filter args + FTS via tsvector + parent expansion.

Search defaults: child + standalone chunks only (parents excluded). Parents
exist only to back parent-document expansion after rerank.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import numpy as np

from app.domain.chunks import Chunk, RetrievalFilters, RetrievedChunk
from app.infra.db import acquire


def _to_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_vec(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


def _filter_clauses(filters: RetrievalFilters | None, start_pos: int) -> tuple[list[str], list]:
    """Build WHERE fragments + params starting at parameter position $start_pos."""
    if filters is None:
        return [], []
    parts: list[str] = []
    params: list = []
    next_pos = start_pos
    if filters.content_types:
        parts.append(f"content_type = ANY(${next_pos})")
        params.append(filters.content_types)
        next_pos += 1
    if filters.labels:
        parts.append(f"labels && ${next_pos}")
        params.append(filters.labels)
        next_pos += 1
    if filters.is_answer is not None:
        parts.append(f"is_answer = ${next_pos}")
        params.append(filters.is_answer)
        next_pos += 1
    if filters.min_closed_at:
        parts.append(f"closed_at >= ${next_pos}")
        params.append(_to_ts(filters.min_closed_at))
        next_pos += 1
    if filters.breadcrumb_prefix:
        parts.append(f"breadcrumb LIKE ${next_pos}")
        params.append(filters.breadcrumb_prefix + "%")
        next_pos += 1
    return parts, params


async def insert_many(chunks: Iterable[Chunk]) -> int:
    rows = [
        (
            c.id,
            c.content_type,
            c.parent_id,
            c.source_id,
            c.chunk_seq,
            c.text,
            _to_vec(c.embedding),
            c.breadcrumb,
            c.section_path,
            c.labels,
            c.is_answer,
            _to_ts(c.closed_at),
        )
        for c in chunks
    ]
    if not rows:
        return 0
    async with acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO chunks (
                id, content_type, parent_id, source_id, chunk_seq, text, embedding,
                breadcrumb, section_path, labels, is_answer, closed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (id) DO NOTHING
            """,
            rows,
        )
    return len(rows)


async def dense_search(
    query_embedding: list[float],
    top_k: int = 5,
    filters: RetrievalFilters | None = None,
    include_parents: bool = False,
) -> list[RetrievedChunk]:
    """Dense top-k via pgvector cosine.

    By default returns child + standalone chunks (chunk_seq >= 0); parent rows
    (chunk_seq = -1) are excluded since parent expansion happens after rerank
    on the retrieval side.
    """
    qvec = _to_vec(query_embedding)
    where_parts: list[str] = []
    params: list = [qvec]
    if not include_parents:
        where_parts.append("chunk_seq >= 0")
    filter_parts, filter_params = _filter_clauses(filters, start_pos=2)
    where_parts.extend(filter_parts)
    params.extend(filter_params)
    params.append(top_k)
    limit_pos = len(params)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = f"""
        SELECT id, content_type, source_id, text, breadcrumb, section_path,
               labels, is_answer,
               1 - (embedding <=> $1) AS score
        FROM chunks
        {where_sql}
        ORDER BY embedding <=> $1
        LIMIT ${limit_pos}
    """
    async with acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_retrieved(r) for r in rows]


async def fts_search(
    query: str,
    top_k: int = 5,
    filters: RetrievalFilters | None = None,
    include_parents: bool = False,
) -> list[RetrievedChunk]:
    """Sparse top-k via Postgres FTS (tsvector + ts_rank_cd)."""
    where_parts: list[str] = []
    params: list = [query]
    if not include_parents:
        where_parts.append("chunk_seq >= 0")
    # tsv matches the query; required for ranking to be meaningful.
    where_parts.append("tsv @@ plainto_tsquery('english', $1)")
    filter_parts, filter_params = _filter_clauses(filters, start_pos=2)
    where_parts.extend(filter_parts)
    params.extend(filter_params)
    params.append(top_k)
    limit_pos = len(params)

    sql = f"""
        SELECT id, content_type, source_id, text, breadcrumb, section_path,
               labels, is_answer,
               ts_rank_cd(tsv, plainto_tsquery('english', $1)) AS score
        FROM chunks
        WHERE {" AND ".join(where_parts)}
        ORDER BY score DESC
        LIMIT ${limit_pos}
    """
    async with acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_retrieved(r) for r in rows]


async def parent_lookup(child_ids: list[str]) -> dict[str, RetrievedChunk]:
    """For each given child chunk id, return the parent chunk (or the child
    itself if it has no parent). Used to expand reranked children to full
    parent context before sending to the LLM.
    """
    if not child_ids:
        return {}
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.id AS child_id,
                COALESCE(p.id, c.id)                       AS id,
                COALESCE(p.content_type, c.content_type)   AS content_type,
                COALESCE(p.source_id, c.source_id)         AS source_id,
                COALESCE(p.text, c.text)                   AS text,
                COALESCE(p.breadcrumb, c.breadcrumb)       AS breadcrumb,
                COALESCE(p.section_path, c.section_path)   AS section_path,
                COALESCE(p.labels, c.labels)               AS labels,
                COALESCE(p.is_answer, c.is_answer)         AS is_answer
            FROM chunks c
            LEFT JOIN chunks p ON p.id = c.parent_id
            WHERE c.id = ANY($1)
            """,
            child_ids,
        )
    out: dict[str, RetrievedChunk] = {}
    for r in rows:
        out[r["child_id"]] = RetrievedChunk(
            id=r["id"],
            content_type=r["content_type"],
            source_id=r["source_id"],
            text=r["text"],
            score=0.0,
            breadcrumb=r["breadcrumb"],
            section_path=r["section_path"],
            labels=list(r["labels"]) if r["labels"] is not None else None,
            is_answer=r["is_answer"],
        )
    return out


def _row_to_retrieved(r) -> RetrievedChunk:
    return RetrievedChunk(
        id=r["id"],
        content_type=r["content_type"],
        source_id=r["source_id"],
        text=r["text"],
        score=float(r["score"]),
        breadcrumb=r["breadcrumb"],
        section_path=r["section_path"],
        labels=list(r["labels"]) if r["labels"] is not None else None,
        is_answer=r["is_answer"],
    )


async def count() -> int:
    async with acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM chunks")
    return int(n or 0)


async def count_by_type() -> dict[str, int]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT content_type, COUNT(*) AS n FROM chunks GROUP BY content_type"
        )
    return {r["content_type"]: int(r["n"]) for r in rows}
