"""Memory endpoints — read-only list for the current principal.

PRD §Chatbot Q17: memory keyed by `user_id` for authed users, by
`widget_session_id` for anonymous widget sessions. Both branches go through
the same repo function via the discriminated kwarg pair.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.auth import AnonWidgetSession, current_principal
from app.repositories import memory as memory_repo


router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=list[dict[str, Any]])
async def list_my_memories(
    limit: int = 100,
    principal=Depends(current_principal),
) -> list[dict[str, Any]]:
    """Return up to `limit` episodic memories for the calling principal."""
    capped = min(limit, 500)
    if isinstance(principal, AnonWidgetSession):
        rows = await memory_repo.list_for_user(
            widget_session_id=principal.widget_session_id, limit=capped
        )
    else:
        rows = await memory_repo.list_for_user(user_id=str(principal.id), limit=capped)
    # asyncpg returns UUID / datetime objects; cast UUID to str for stability.
    for r in rows:
        r["id"] = str(r["id"])
    return rows
