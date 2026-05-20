"""fastapi-users user model + adapter.

The `users` table is owned by SQLAlchemy because fastapi-users requires it.
All other tables in this app are accessed via raw asyncpg — see ARCH.md.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.infra.sa_db import get_async_session


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Maps to the `users` table created in migration 003.

    fastapi-users base provides: id (UUID), email, hashed_password,
    is_active, is_superuser, is_verified. We add `role`.
    """

    __tablename__ = "users"

    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncIterator[SQLAlchemyUserDatabase]:
    yield SQLAlchemyUserDatabase(session, User)


UserID = uuid.UUID
