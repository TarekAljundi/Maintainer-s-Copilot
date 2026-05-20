"""Shared test fixtures.

Integration tests that need a running Postgres skip cleanly if the DB isn't
reachable. Local dev workflow: `docker compose up -d db` brings up the DB
container alone, then `pytest -m integration` runs the gated suite.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

import pytest
import pytest_asyncio


def _pg_dsn() -> str:
    user = os.environ.get("POSTGRES_USER", "copilot")
    db = os.environ.get("POSTGRES_DB", "copilot")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    auth = f":{pw}" if pw else ""
    return f"postgresql://{user}{auth}@{host}:{port}/{db}"


async def _pg_reachable() -> bool:
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(_pg_dsn()), timeout=2.0)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    return _pg_dsn()


@pytest_asyncio.fixture
async def pg_available() -> bool:
    return await _pg_reachable()


@pytest.fixture(autouse=False)
def skip_if_no_pg(pg_available: bool) -> None:
    if not pg_available:
        pytest.skip(f"Postgres not reachable at {_pg_dsn()} — start `docker compose up -d db`.")


@pytest_asyncio.fixture
async def pg_pool(pg_available: bool, pg_dsn: str) -> AsyncIterator:
    if not pg_available:
        pytest.skip("Postgres not reachable.")
    import asyncpg

    async def _init(conn):
        # Register pgvector codec so test pool matches production
        # (app/infra/db.py registers the same on _init_conn).
        from pgvector.asyncpg import register_vector

        await register_vector(conn)

    pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=2, init=_init)
    try:
        yield pool
    finally:
        await pool.close()
