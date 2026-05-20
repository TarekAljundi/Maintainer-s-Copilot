"""Memory endpoints — read-only list for the current user."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import AnonWidgetSession, current_principal
from app.repositories import memory as memory_repo


router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=list[dict[str, Any]])
async def list_my_memories(
    limit: int = 100,
    principal=Depends(current_principal),
) -> list[dict[str, Any]]:
    """Return up to `limit` episodic memories belonging to the current user.

    Widget sessions don't have memory in this slice (PRD §Chatbot Q17 says
    memory keys by user_id for authed users; widget_session_id keying is
    slice-13 scope).
    """
    if isinstance(principal, AnonWidgetSession):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="widget sessions have no episodic memory",
        )
    rows = await memory_repo.list_for_user(str(principal.id), limit=min(limit, 500))
    # asyncpg returns UUID / datetime objects; FastAPI's default encoder handles
    # both, but cast UUID to str for stability across drivers.
    for r in rows:
        r["id"] = str(r["id"])
    return rows
