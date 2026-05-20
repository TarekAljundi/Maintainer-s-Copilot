"""SQL for episodic_memories + companion audit-row insert.

PRD §Chatbot Q17 schema (post slice 13 migration 004):
    (id, user_id NULL, widget_session_id VARCHAR(64) NULL,
     conversation_id, memory_type='episodic', summary, entities[],
     source_msg_ids[], embedding VECTOR(768), created_at, last_recalled_at)

A row is scoped by exactly one of `user_id` (authed) or `widget_session_id`
(anonymous widget). The repo accepts a discriminated kwarg pair and routes the
WHERE/INSERT accordingly; the table-level CHECK enforces at-least-one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.infra.db import acquire
from app.repositories.audit import write_audit


def _encode_embedding(vec):
    """Encode a list[float] for an asyncpg VECTOR column."""
    try:
        import numpy as np

        return np.asarray(vec, dtype=np.float32)
    except ImportError:
        return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _resolve_actor(user_id: str | None, widget_session_id: str | None) -> tuple[str, str | None]:
    """Validate the discriminator and return (audit_actor, none_branch).

    audit_actor is the string written to audit_log.actor — `<uuid>` for users,
    `widget_session:<sid>` for anon sessions. Raises ValueError if neither or
    both are set.
    """
    if user_id and widget_session_id:
        raise ValueError("set exactly one of user_id or widget_session_id, not both")
    if not user_id and not widget_session_id:
        raise ValueError("memory write requires user_id or widget_session_id")
    if user_id:
        return user_id, "user"
    return f"widget_session:{widget_session_id}", "widget"


async def insert_memory_with_audit(
    *,
    user_id: str | None = None,
    widget_session_id: str | None = None,
    conversation_id: str | None,
    summary: str,
    entities: list[str] | None,
    source_msg_ids: list[str] | None,
    embedding: list[float],
) -> str:
    """Insert memory + audit rows in a single transaction; either authed or
    widget-anon depending on which discriminator is set.
    """
    audit_actor, branch = _resolve_actor(user_id, widget_session_id)
    async with acquire() as conn:
        async with conn.transaction():
            if branch == "user":
                mid = await conn.fetchval(
                    """
                    INSERT INTO episodic_memories
                        (user_id, conversation_id, summary, entities, source_msg_ids, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    UUID(user_id),  # type: ignore[arg-type]
                    conversation_id,
                    summary,
                    entities,
                    source_msg_ids,
                    _encode_embedding(embedding),
                )
            else:
                mid = await conn.fetchval(
                    """
                    INSERT INTO episodic_memories
                        (widget_session_id, conversation_id, summary, entities,
                         source_msg_ids, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                    """,
                    widget_session_id,
                    conversation_id,
                    summary,
                    entities,
                    source_msg_ids,
                    _encode_embedding(embedding),
                )
            await write_audit(
                actor=audit_actor,
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
    user_id: str | None = None,
    widget_session_id: str | None = None,
    query_embedding: list[float],
    top_k: int,
    min_similarity: float,
) -> list[dict[str, Any]]:
    """Cosine-similarity top-k scoped by whichever discriminator is set."""
    _, branch = _resolve_actor(user_id, widget_session_id)
    max_distance = 1.0 - min_similarity
    async with acquire() as conn:
        if branch == "user":
            rows = await conn.fetch(
                """
                SELECT id, user_id, conversation_id, summary, entities, source_msg_ids,
                       created_at, last_recalled_at,
                       1 - (embedding <=> $2) AS similarity
                FROM episodic_memories
                WHERE user_id = $1
                  AND (embedding <=> $2) <= $4
                ORDER BY embedding <=> $2 ASC
                LIMIT $3
                """,
                UUID(user_id),  # type: ignore[arg-type]
                _encode_embedding(query_embedding),
                top_k,
                max_distance,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, widget_session_id, conversation_id, summary, entities,
                       source_msg_ids, created_at, last_recalled_at,
                       1 - (embedding <=> $2) AS similarity
                FROM episodic_memories
                WHERE widget_session_id = $1
                  AND (embedding <=> $2) <= $4
                ORDER BY embedding <=> $2 ASC
                LIMIT $3
                """,
                widget_session_id,
                _encode_embedding(query_embedding),
                top_k,
                max_distance,
            )
    return [dict(r) for r in rows]


async def touch_last_recalled(ids: list[str]) -> None:
    if not ids:
        return
    async with acquire() as conn:
        await conn.execute(
            "UPDATE episodic_memories SET last_recalled_at = now() WHERE id = ANY($1::uuid[])",
            [UUID(i) for i in ids],
        )


async def list_for_user(
    *,
    user_id: str | None = None,
    widget_session_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _, branch = _resolve_actor(user_id, widget_session_id)
    async with acquire() as conn:
        if branch == "user":
            rows = await conn.fetch(
                """
                SELECT id, conversation_id, summary, entities, created_at, last_recalled_at
                FROM episodic_memories
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                UUID(user_id),  # type: ignore[arg-type]
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, conversation_id, summary, entities, created_at, last_recalled_at
                FROM episodic_memories
                WHERE widget_session_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                widget_session_id,
                limit,
            )
    return [dict(r) for r in rows]
