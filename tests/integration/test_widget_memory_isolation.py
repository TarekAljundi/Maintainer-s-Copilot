"""Mandated test (slice 13): a widget session can write+recall its OWN
memories across conversations; a sibling widget session NEVER sees them.

Runs against the real Postgres+pgvector container (skipped otherwise — same
pattern as test_memory_pg.py). Uses a deterministic fake embedder so we
don't need the model server.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio


pytestmark = pytest.mark.asyncio


def _vec(seed: float) -> list[float]:
    return [seed] * 768


class _FakeEmbed:
    """Two strings with the same hash bucket → same vector → cosine 1.0."""

    def embed(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
        out = []
        for t in texts:
            seed = (sum(ord(c) for c in t) % 5 + 1) * 0.1
            out.append(_vec(seed))
        return out


@pytest_asyncio.fixture
async def app_db_pool(pg_pool, monkeypatch):
    """Route app code to the same DB the fixture opened."""
    from app.infra import db as app_db

    monkeypatch.setattr(app_db, "_pool", pg_pool)
    yield


async def test_widget_session_write_recall_self_only(pg_pool, app_db_pool):
    from app.services.memory import MemoryService

    svc = MemoryService(model_client=_FakeEmbed())
    sid_a = uuid.uuid4().hex
    sid_b = uuid.uuid4().hex

    # Widget A writes a memory in conv 1.
    mid_a = await svc.write(
        widget_session_id=sid_a,
        summary="Widget A interested in groupby NaN handling.",
        entities=["groupby"],
        conversation_id="conv-1",
    )
    # Widget B writes a different memory.
    mid_b = await svc.write(
        widget_session_id=sid_b,
        summary="Widget B looking at IO csv chunksize edge cases.",
        entities=["io"],
        conversation_id="conv-1",
    )

    try:
        # Widget A recalls in a different conversation — must see its own.
        hits_a = await svc.recall(
            widget_session_id=sid_a,
            query="groupby NaN handling",
            top_k=5,
            min_similarity=0.0,
        )
        ids_a = {h.id for h in hits_a}
        assert mid_a in ids_a, "widget A failed to recall its own memory"

        # Widget B recalls A's query topic — must NOT see A's memory.
        hits_b = await svc.recall(
            widget_session_id=sid_b,
            query="groupby NaN handling",
            top_k=5,
            min_similarity=0.0,
        )
        ids_b = {h.id for h in hits_b}
        assert mid_a not in ids_b, "widget B saw widget A's memory (isolation broken)"
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM episodic_memories WHERE id = ANY($1::uuid[])",
                [uuid.UUID(mid_a), uuid.UUID(mid_b)],
            )


async def test_widget_session_isolated_from_authed_user(pg_pool, app_db_pool):
    """A widget session and an authed user with the same conv id MUST NOT
    share memory rows."""
    from app.services.memory import MemoryService

    svc = MemoryService(model_client=_FakeEmbed())

    user_id = str(uuid.uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, hashed_password, is_active, role) "
            "VALUES ($1, $2, $3, TRUE, 'user')",
            uuid.UUID(user_id),
            f"u-{user_id[:8]}@example.com",
            "$2b$12$placeholder",
        )
    sid = uuid.uuid4().hex

    user_mid = await svc.write(user_id=user_id, summary="user-only memo about indexing")
    widget_mid = await svc.write(widget_session_id=sid, summary="widget-only memo about indexing")

    try:
        hits_user = await svc.recall(user_id=user_id, query="indexing", top_k=5, min_similarity=0.0)
        hits_widget = await svc.recall(
            widget_session_id=sid, query="indexing", top_k=5, min_similarity=0.0
        )
        assert widget_mid not in {h.id for h in hits_user}
        assert user_mid not in {h.id for h in hits_widget}
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM episodic_memories WHERE id = ANY($1::uuid[])",
                [uuid.UUID(user_mid), uuid.UUID(widget_mid)],
            )
            await conn.execute("DELETE FROM users WHERE id = $1", uuid.UUID(user_id))
