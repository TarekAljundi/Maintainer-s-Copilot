"""Memory integration tests against a real Postgres + pgvector.

Skipped if Postgres isn't reachable. Run locally with:
    docker compose up -d db
    POSTGRES_HOST=localhost POSTGRES_PORT=<host-port> pytest tests/integration/test_memory_pg.py

PRD §Chatbot Q17 + §Tier 2 tests — MemoryService:
- write inserts memory + audit in same tx
- recall is user_id-scoped (user A cannot see user B's memories)
- redaction applied before persistence
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio


pytestmark = pytest.mark.asyncio


# ---------- Helpers ------------------------------------------------------


def _vec(seed: float = 0.1) -> list[float]:
    """Deterministic 768-d vector; two seeds give two distinct vectors that
    are nonetheless similar enough to register as a hit for the same query."""
    return [seed] * 768


class _FakeEmbed:
    """Embeds 'passage' texts and 'query' texts to the same vector when their
    content overlaps — sufficient for cosine similarity testing without spinning
    up the model server. Returns a 768-d vec derived from a stable hash."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts: list[str], mode: str = "passage") -> list[list[float]]:
        self.calls.append((list(texts), mode))
        # Both 'passage' and 'query' for the same text → same vector → similarity 1.0.
        # Slightly different text → still high similarity because the seed
        # depends only on hash mod 5.
        out = []
        for t in texts:
            seed = (sum(ord(c) for c in t) % 5 + 1) * 0.1
            out.append(_vec(seed))
        return out


@pytest_asyncio.fixture
async def isolated_user(pg_pool):
    """Create a throwaway user row and yield its id; clean up after the test.

    Also ensures the pool that the application code uses points at the same
    DB the fixture pool opened against.
    """
    user_id = str(uuid.uuid4())
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, hashed_password, is_active, role) "
            "VALUES ($1, $2, $3, TRUE, 'user')",
            uuid.UUID(user_id),
            f"u-{user_id[:8]}@example.com",
            "$2b$12$placeholder",
        )
    yield user_id
    async with pg_pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE id = $1", uuid.UUID(user_id))


@pytest_asyncio.fixture
async def app_db_pool(pg_pool, monkeypatch):
    """Make app.infra.db.acquire() use the test pool so service code sees the
    same DB the fixtures opened against."""
    from app.infra import db as app_db

    monkeypatch.setattr(app_db, "_pool", pg_pool)
    yield
    # Don't close pg_pool here — the pg_pool fixture owns it.


# ---------- Tests --------------------------------------------------------


async def test_write_persists_redacted_summary(
    pg_pool, isolated_user: str, app_db_pool, monkeypatch
):
    """End-to-end: write a memory containing a secret, observe the persisted
    row's summary column carries [REDACTED:...] not the raw token."""
    from app.services.memory import MemoryService

    svc = MemoryService(model_client=_FakeEmbed())
    raw_token = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"
    mid = await svc.write(
        user_id=isolated_user,
        summary=f"User stashed an API token: {raw_token}. Investigate.",
        entities=["secret_test"],
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT summary, entities FROM episodic_memories WHERE id = $1",
            uuid.UUID(mid),
        )
    assert row is not None
    assert raw_token not in row["summary"]
    assert "[REDACTED:github_token]" in row["summary"]


async def test_write_and_audit_in_single_transaction(
    pg_pool, isolated_user: str, app_db_pool
):
    """AC: memory row + audit row land in the same transaction."""
    from app.services.memory import MemoryService

    svc = MemoryService(model_client=_FakeEmbed())
    mid = await svc.write(
        user_id=isolated_user,
        summary="Focused on middleware ordering this week.",
        entities=["middleware"],
        conversation_id="conv-A",
    )

    async with pg_pool.acquire() as conn:
        memory_count = await conn.fetchval(
            "SELECT count(*) FROM episodic_memories WHERE id = $1", uuid.UUID(mid)
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM audit_log "
            "WHERE actor = $1 AND action = 'memory_write' AND target_id = $2",
            isolated_user,
            mid,
        )
    assert memory_count == 1
    assert audit_count == 1


async def test_recall_is_user_scoped(pg_pool, app_db_pool):
    """User A's recall must never return user B's memories."""
    from app.services.memory import MemoryService

    uid_a = str(uuid.uuid4())
    uid_b = str(uuid.uuid4())
    async with pg_pool.acquire() as conn:
        for uid in (uid_a, uid_b):
            await conn.execute(
                "INSERT INTO users (id, email, hashed_password, is_active, role) "
                "VALUES ($1, $2, $3, TRUE, 'user')",
                uuid.UUID(uid),
                f"u-{uid[:8]}@example.com",
                "$2b$12$placeholder",
            )
    try:
        svc = MemoryService(model_client=_FakeEmbed())
        await svc.write(
            user_id=uid_a, summary="User A: deep into Indexing.copy issue."
        )
        await svc.write(
            user_id=uid_b, summary="User B: focused on Series.dt timezone bugs."
        )

        # User B queries semantically close to A's memory.
        hits_b = await svc.recall(
            user_id=uid_b,
            query="Indexing.copy",
            top_k=5,
            min_similarity=0.0,  # widest possible window
        )
        ids = {h.id for h in hits_b}
        # B should at most see B's own row, never A's row.
        async with pg_pool.acquire() as conn:
            a_ids = {
                str(r["id"])
                for r in await conn.fetch(
                    "SELECT id FROM episodic_memories WHERE user_id = $1",
                    uuid.UUID(uid_a),
                )
            }
        assert ids.isdisjoint(a_ids), "user B saw user A's memory"
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::uuid[])",
                [uuid.UUID(uid_a), uuid.UUID(uid_b)],
            )


async def test_cross_conversation_recall_same_user(
    pg_pool, isolated_user: str, app_db_pool
):
    """PRD cross-conv demo: write in conv A, recall in conv B (same user)."""
    from app.services.memory import MemoryService

    svc = MemoryService(model_client=_FakeEmbed())
    await svc.write(
        user_id=isolated_user,
        summary="I'm focused on middleware-order regressions this week.",
        conversation_id="conv-A",
    )

    # New conversation, same user — recall should still find the memory.
    hits = await svc.recall(
        user_id=isolated_user,
        query="what was I working on",
        top_k=5,
        min_similarity=0.0,
    )
    # The conv_id is part of the row; recall is unscoped by conversation.
    assert any("middleware" in h.summary for h in hits), (
        f"cross-conversation recall did not return the prior memory: "
        f"{[(h.conversation_id, h.summary) for h in hits]}"
    )


async def test_recall_writes_audit_row(pg_pool, isolated_user: str, app_db_pool):
    """recall() writes an audit row with action='memory_recall' (best-effort).

    Per slice plan §C: the audit row is best-effort but should normally land.
    """
    from app.services.memory import MemoryService

    svc = MemoryService(model_client=_FakeEmbed())
    await svc.write(user_id=isolated_user, summary="Marker memory for audit test.")

    hits = await svc.recall(
        user_id=isolated_user, query="marker", top_k=5, min_similarity=0.0
    )
    assert hits, "test prerequisite: at least one hit"

    async with pg_pool.acquire() as conn:
        recall_count = await conn.fetchval(
            "SELECT count(*) FROM audit_log "
            "WHERE actor = $1 AND action = 'memory_recall'",
            isolated_user,
        )
    assert recall_count >= 1
