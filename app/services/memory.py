"""MemoryService — episodic long-term memory.

PRD §Chatbot Q17 + §Tier 2 tests + slice 11 issue.

Interface:
    write(user_id, summary, entities, ...) -> memory_id
    recall(user_id, query, top_k=5, min_similarity=0.6) -> list[Memory]

Boundary-3 redaction is applied to `summary` BEFORE embedding so the raw
secret never reaches the vector either. Memory + audit rows land in the
same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.exceptions import MemoryWriteFailure
from app.infra import tracing
from app.infra.model_server_client import ModelServerClient
from app.infra.redaction import redact
from app.repositories import memory as memory_repo
from app.repositories.audit import write_audit


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    id: str
    summary: str
    entities: list[str]
    conversation_id: str | None
    similarity: float
    created_at: Any
    last_recalled_at: Any | None


class MemoryService:
    def __init__(self, model_client: ModelServerClient | None = None) -> None:
        self._models = model_client or ModelServerClient()

    @tracing.observe(as_type="memory", name="memory.write")
    async def write(
        self,
        *,
        user_id: str,
        summary: str,
        entities: list[str] | None = None,
        source_msg_ids: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """Embed + persist a memory row + audit row atomically.

        Returns the new memory id. Wraps repository / model-server failures
        as `MemoryWriteFailure` so the chatbot loop converts them to the
        `{ok:false, error:"tool_failure.memory"}` envelope.
        """
        if not summary or not summary.strip():
            raise MemoryWriteFailure("summary is empty")

        # Boundary 3: redact before embedding so the vector never carries
        # the raw secret either.
        redacted = redact(summary)

        try:
            embeddings = self._models.embed([redacted], mode="passage")
        except Exception as exc:
            raise MemoryWriteFailure(f"embed failed: {exc}") from exc
        if not embeddings or len(embeddings[0]) != 768:
            raise MemoryWriteFailure("model-server returned malformed embedding")

        try:
            mid = await memory_repo.insert_memory_with_audit(
                user_id=user_id,
                conversation_id=conversation_id,
                summary=redacted,
                entities=entities,
                source_msg_ids=source_msg_ids,
                embedding=embeddings[0],
            )
        except Exception as exc:
            raise MemoryWriteFailure(f"db insert failed: {exc}") from exc

        return mid

    @tracing.observe(as_type="memory", name="memory.recall")
    async def recall(
        self,
        *,
        user_id: str,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.6,
    ) -> list[RecalledMemory]:
        """Top-k user-scoped recall by cosine similarity.

        Recall failures are not fatal — callers (the chatbot's auto-recall
        hook) treat an empty list as "no memories" and proceed. Errors are
        re-raised here so tests can observe them; the chatbot wraps in a
        try/except.
        """
        if not query.strip():
            return []

        embeddings = self._models.embed([query], mode="query")
        if not embeddings:
            return []

        rows = await memory_repo.find_similar(
            user_id=user_id,
            query_embedding=embeddings[0],
            top_k=top_k,
            min_similarity=min_similarity,
        )

        result = [
            RecalledMemory(
                id=str(r["id"]),
                summary=r["summary"],
                entities=list(r["entities"] or []),
                conversation_id=r["conversation_id"],
                similarity=float(r["similarity"]),
                created_at=r["created_at"],
                last_recalled_at=r["last_recalled_at"],
            )
            for r in rows
        ]

        if result:
            ids = [m.id for m in result]
            try:
                await memory_repo.touch_last_recalled(ids)
            except Exception:
                # Best-effort: the recall is still valid even if we couldn't
                # bump last_recalled_at.
                pass
            try:
                await write_audit(
                    actor=user_id,
                    action="memory_recall",
                    target_type="memory",
                    target_id=None,
                    payload={"ids": ids, "top_k": top_k, "min_similarity": min_similarity},
                )
            except Exception:
                # Audit logging is a side-effect, not a correctness gate for the
                # chat turn (per slice plan §C).
                pass

        return result


_default: MemoryService | None = None


def default_service() -> MemoryService:
    global _default
    if _default is None:
        _default = MemoryService()
    return _default
