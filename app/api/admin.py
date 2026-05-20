"""Admin endpoints. PRD user stories 21, 22.

User story 21: admin invites new users by email.
User story 22: admin views audit log.

Widget CRUD lands with slice 13.
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel, EmailStr

from app.api.auth import UserCreate, get_user_manager, require_admin
from app.repositories.audit import list_for_actor, write_audit
from app.repositories.users import User


router = APIRouter(prefix="/admin", tags=["admin"])


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "user"


class InviteResponse(BaseModel):
    user_id: str
    email: str
    initial_password: str
    role: str


@router.post("/invite", response_model=InviteResponse)
async def invite_user(
    body: InviteRequest,
    admin: User = Depends(require_admin),
    manager=Depends(get_user_manager),
) -> InviteResponse:
    """Create a user with a randomly generated initial password.

    The admin hands the password to the invitee out of band. Verification
    + first-login password rotation are out of slice scope (PRD §Out of Scope —
    no SMTP for password reset).
    """
    if body.role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="role must be 'user' or 'admin'",
        )
    pw = secrets.token_urlsafe(16)
    create = UserCreate(email=body.email, password=pw, role=body.role)
    try:
        user = await manager.create(create)
    except UserAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="email already exists"
        ) from exc

    await write_audit(
        actor=str(admin.id),
        action="invite_user",
        target_type="user",
        target_id=str(user.id),
        payload={"email": body.email, "role": body.role},
    )

    return InviteResponse(
        user_id=str(user.id),
        email=body.email,
        initial_password=pw,
        role=body.role,
    )


@router.get("/audit", response_model=list[dict[str, Any]])
async def audit_log_for_actor(
    actor: str,
    limit: int = 100,
    admin: User = Depends(require_admin),
) -> list[dict[str, Any]]:
    """Admin-only audit log view scoped to one actor."""
    return await list_for_actor(actor, limit=min(limit, 1000))
