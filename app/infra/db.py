"""Async Postgres connection pool with pgvector codec."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    user = os.environ.get("POSTGRES_USER", "copilot")
    db = os.environ.get("POSTGRES_DB", "copilot")
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT_INTERNAL", os.environ.get("POSTGRES_PORT", "5432"))
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    auth = f":{pw}" if pw else ""
    return f"postgresql://{user}{auth}@{host}:{port}/{db}"


async def _init_conn(conn: asyncpg.Connection) -> None:
    from pgvector.asyncpg import register_vector

    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _dsn(),
            min_size=1,
            max_size=4,
            init=_init_conn,
        )
    return _pool


@asynccontextmanager
async def acquire() -> AsyncIterator[asyncpg.pool.PoolConnectionProxy]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
