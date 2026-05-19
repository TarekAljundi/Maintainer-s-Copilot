"""Alembic env. Async SQLAlchemy with asyncpg."""
from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

config = context.config


def _db_url() -> str:
    user = os.environ.get("POSTGRES_USER", "copilot")
    db = os.environ.get("POSTGRES_DB", "copilot")
    host = os.environ.get("POSTGRES_HOST", "db")
    port = os.environ.get("POSTGRES_PORT_INTERNAL", "5432")
    return f"postgresql+asyncpg://{user}@{host}:{port}/{db}"


config.set_main_option("sqlalchemy.url", _db_url())
target_metadata = None  # tables come from future slices' models


def run_migrations_offline() -> None:
    context.configure(url=_db_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as conn:
        await conn.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
