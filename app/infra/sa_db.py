"""SQLAlchemy async engine + session — fastapi-users boundary only.

The rest of the app uses raw asyncpg via `app/infra/db.py`. fastapi-users
requires a SQLAlchemy session for `SQLAlchemyUserDatabase`. Running both is
acceptable because the SQLAlchemy footprint is exactly one table (`users`);
all other tables are accessed via asyncpg.

DSN is loaded from Vault `api/db` (asyncpg-flavored URL). For the SQLAlchemy
side we swap the driver to `postgresql+asyncpg://...`.
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infra.vault import get_vault


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_dsn() -> str:
    secrets = get_vault().cached("api/db")
    url = secrets.get("url") or ""
    if not url:
        raise RuntimeError("api/db.url missing from vault")
    # vault_seed.sh stores `postgresql+asyncpg://...` which is what we want.
    return url


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(_build_dsn(), pool_size=2, max_overflow=2)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def get_async_session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session
