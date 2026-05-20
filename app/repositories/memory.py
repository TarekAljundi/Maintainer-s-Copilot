"""SQL for episodic_memories + companion audit-row insert.

PRD §Chatbot Q17 schema:
    (id, user_id, conversation_id, memory_type='episodic', summary, entities[],
     source_msg_ids[], embedding VECTOR(768), created_at, last_recalled_at)
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.infra.db import acquire
from app.repositories.audit import write_audit


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts text input `[1.0, 2.0, ...]` for VECTOR(N) columns."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def insert_memory_with_audit(
    *,
    user_id: str,
    conversation_id: str | None,
    summary: str,
    entities: list[str] | None,
    source_msg_ids: list[str] | None,
    embedding: list[float],
) -> str:
    """Insert memory row + audit row in a single transaction.

    Returns the memory id (UUID string). Raises asyncpg.PostgresError on
    failure (the service layer maps that to MemoryWriteFailure).
    """
    async with acquire() as conn:
        async with conn.transaction():
            mid = await conn.fetchval(
                """
                INSERT INTO episodic_memories
                    (user_id, conversation_id, summary, entities, source_msg_ids, embedding)
                VALUES ($1, $2, $3, $4, $5, $6::vector)
                RETURNING id
                """,
                UUID(user_id),
                conversation_id,
                summary,
                entities,
                source_msg_ids,
                _vector_literal(embedding),
            )
            await write_audit(
                actor=user_id,
                action="memory_write",
                target_type="memory",
                target_id=str(mid),
                payload={
                    "conversation_id": conversation_id,
                    "entity_count": len(entities or []),
                },
                conn=conn,
            )
    return str(mid)


async def find_similar(
    *,
    user_id: str,
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
) -> list[dict[str, Any]]:
    """Cosine-similarity top-k, user-scoped, filtered by min_similarity.

    pgvector's `<=>` is cosine distance (0=identical, 2=opposite). We convert
    to similarity = 1 - distance and filter `>= min_similarity`.
    """
    rows = await _execute_similarity(user_id, query_embedding, top_k, min_similarity)
    return [dict(r) for r in rows]


async def _execute_similarity(
    user_id: str, query_embedding: list[float], top_k: int, min_similarity: float
):
    max_distance = 1.0 - min_similarity
    async with acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, user_id, conversation_id, summary, entities, source_msg_ids,
                   created_at, last_recalled_at,
                   1 - (embedding <=> $2::vector) AS similarity
            FROM episodic_memories
            WHERE user_id = $1
              AND (embedding <=> $2::vector) <= $4
            ORDER BY embedding <=> $2::vector ASC
            LIMIT $3
            """,
            UUID(user_id),
            _vector_literal(query_embedding),
            top_k,
            max_distance,
        )


async def touch_last_recalled(ids: list[str]) -> None:
    if not ids:
        return
    async with acquire() as conn:
        await conn.execute(
            "UPDATE episodic_memories SET last_recalled_at = now() WHERE id = ANY($1::uuid[])",
            [UUID(i) for i in ids],
        )


async def list_for_user(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    async with acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, conversation_id, summary, entities, created_at, last_recalled_at
            FROM episodic_memories
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            UUID(user_id),
            limit,
        )
    return [dict(r) for r in rows]
