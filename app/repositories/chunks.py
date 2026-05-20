"""SQL for chunks: bulk insert + dense top-k via pgvector cosine."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import numpy as np

from app.domain.chunks import Chunk, RetrievedChunk
from app.infra.db import acquire


def _to_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_vec(values: list[float]) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


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


async def dense_search(query_embedding: list[float], top_k: int = 5) -> list[RetrievedChunk]:
    qvec = _to_vec(query_embedding)
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, content_type, source_id, text, breadcrumb, section_path,
                   labels, is_answer,
                   1 - (embedding <=> $1) AS score
            FROM chunks
            ORDER BY embedding <=> $1
            LIMIT $2
            """,
            qvec,
            top_k,
        )
    return [
        RetrievedChunk(
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
        for r in rows
    ]


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
