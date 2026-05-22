"""Admin endpoints. PRD user stories 21, 22, 26, 27.

- 21: admin invites new users by email.
- 22: admin views audit log.
- 26/27 (slice 13): admin creates / edits / lists widget configs and copies the
  embed snippet.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi_users.exceptions import UserAlreadyExists
from pydantic import BaseModel, EmailStr, Field

from app.api.auth import UserCreate, get_user_manager, require_admin
from app.domain.exceptions import NotFoundError
from app.domain.widget import ALLOWED_POSITIONS, DEFAULT_ENABLED_TOOLS, WidgetConfig
from app.domain.widget_themes import DEFAULT_THEME, THEMES
from app.repositories.audit import list_for_actor, write_audit
from app.repositories.users import User
from app.services.widget_config import WidgetConfigService, default_service as widget_svc


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


# ---- Widget config CRUD (slice 13) ---------------------------------------


class WidgetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    allowed_origins: list[str] = Field(default_factory=list)
    primary_color: str = "#1e293b"
    position: str = "br"
    greeting_text: str = "Hi! Ask me anything about this project."
    enabled_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_ENABLED_TOOLS))
    theme: str = DEFAULT_THEME


class WidgetUpdate(BaseModel):
    name: str | None = None
    allowed_origins: list[str] | None = None
    primary_color: str | None = None
    position: str | None = None
    greeting_text: str | None = None
    enabled_tools: list[str] | None = None
    theme: str | None = None


class WidgetView(BaseModel):
    id: str
    name: str
    allowed_origins: list[str]
    primary_color: str
    position: str
    greeting_text: str
    enabled_tools: list[str]
    theme: str


def _to_view(c: WidgetConfig) -> WidgetView:
    return WidgetView(
        id=c.id,
        name=c.name,
        allowed_origins=list(c.allowed_origins),
        primary_color=c.primary_color,
        position=c.position,
        greeting_text=c.greeting_text,
        enabled_tools=list(c.enabled_tools),
        theme=c.theme,
    )


def _service() -> WidgetConfigService:
    return widget_svc()


def _normalize_origins(origins: list[str]) -> list[str]:
    """Strip trailing slashes + whitespace + drop empties so CSP/CORS
    comparisons (which use the browser's `window.location.origin`, no trailing
    slash) match exactly."""
    cleaned: list[str] = []
    for o in origins:
        s = (o or "").strip().rstrip("/")
        if s:
            cleaned.append(s)
    return cleaned


def _public_base(request: Request) -> str:
    """Base URL to embed in copy-paste snippets. `PUBLIC_API_BASE` env wins;
    else fall back to the admin's `Host` header so local dev works zero-config."""
    override = os.environ.get("PUBLIC_API_BASE", "").strip().rstrip("/")
    if override:
        return override
    host = request.headers.get("host") or "localhost:8000"
    scheme = "https" if request.url.scheme == "https" else "http"
    return f"{scheme}://{host}"


@router.post("/widgets", response_model=WidgetView, status_code=201)
async def create_widget(
    body: WidgetCreate,
    request: Request,
    admin: User = Depends(require_admin),
) -> WidgetView:
    if body.position not in ALLOWED_POSITIONS:
        raise HTTPException(
            status_code=422, detail=f"position must be one of {list(ALLOWED_POSITIONS)}"
        )
    if body.theme not in THEMES:
        raise HTTPException(status_code=422, detail=f"theme must be one of {list(THEMES)}")
    cfg = await _service().create(
        name=body.name,
        allowed_origins=_normalize_origins(body.allowed_origins),
        primary_color=body.primary_color,
        position=body.position,
        greeting_text=body.greeting_text,
        enabled_tools=body.enabled_tools,
        theme=body.theme,
    )
    await write_audit(
        actor=str(admin.id),
        action="widget_config_create",
        target_type="widget_config",
        target_id=cfg.id,
        payload={"name": cfg.name, "allowed_origins": list(cfg.allowed_origins)},
    )
    return _to_view(cfg)


@router.get("/widgets", response_model=list[WidgetView])
async def list_widgets(
    limit: int = 100,
    offset: int = 0,
    admin: User = Depends(require_admin),
) -> list[WidgetView]:
    rows = await _service().list_(limit=min(limit, 500), offset=max(offset, 0))
    return [_to_view(r) for r in rows]


@router.get("/widgets/{widget_id}", response_model=WidgetView)
async def get_widget(
    widget_id: str,
    admin: User = Depends(require_admin),
) -> WidgetView:
    try:
        cfg = await _service().get(widget_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return _to_view(cfg)


@router.patch("/widgets/{widget_id}", response_model=WidgetView)
async def patch_widget(
    widget_id: str,
    body: WidgetUpdate,
    admin: User = Depends(require_admin),
) -> WidgetView:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if "allowed_origins" in patch:
        patch["allowed_origins"] = _normalize_origins(patch["allowed_origins"])
    if "position" in patch and patch["position"] not in ALLOWED_POSITIONS:
        raise HTTPException(
            status_code=422, detail=f"position must be one of {list(ALLOWED_POSITIONS)}"
        )
    if "theme" in patch and patch["theme"] not in THEMES:
        raise HTTPException(status_code=422, detail=f"theme must be one of {list(THEMES)}")
    try:
        cfg = await _service().update(widget_id, **patch)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    await write_audit(
        actor=str(admin.id),
        action="widget_config_update",
        target_type="widget_config",
        target_id=widget_id,
        payload={"patch": patch},
    )
    return _to_view(cfg)


@router.delete("/widgets/{widget_id}", status_code=204)
async def delete_widget(
    widget_id: str,
    admin: User = Depends(require_admin),
) -> None:
    ok = await _service().delete(widget_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"widget {widget_id!r} not found")
    await write_audit(
        actor=str(admin.id),
        action="widget_config_delete",
        target_type="widget_config",
        target_id=widget_id,
        payload={},
    )


@router.get("/widgets/{widget_id}/embed-snippet")
async def embed_snippet(
    widget_id: str,
    request: Request,
    admin: User = Depends(require_admin),
) -> dict[str, str]:
    try:
        await _service().get(widget_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    base = _public_base(request)
    snippet = f'<script src="{base}/widget.js" data-widget-id="{widget_id}"></script>'
    return {"snippet": snippet, "src": f"{base}/widget.js", "widget_id": widget_id}
