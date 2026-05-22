"""Widget public endpoints — anonymous-accessible (no JWT) routes used by the
embeddable widget bundle + the iframe host page.

Routes:
    GET  /widget/{id}/config   public-read of the WidgetPublicConfig view
                               (no `allowed_origins` — leak surface).
    POST /widget/{id}/session  mints an anon JWT scoped to the widget.
    GET  /widget/{id}/embed    HTML with `Content-Security-Policy:
                               frame-ancestors <allowed_origins>` from the DB.
                               The iframe content is the Preact bundle (served
                               by an external static host in dev/prod).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.domain.exceptions import NotFoundError
from app.domain.widget import WidgetPublicConfig
from app.domain.widget_themes import resolve_theme
from app.services.anon_session import default_service as anon_svc
from app.services.widget_config import default_service as widget_svc

router = APIRouter(prefix="/widget", tags=["widget"])


def _bundle_base() -> str:
    """Where the Vite-built widget bundle is served from. Default targets the
    `widget` nginx container in docker-compose (port 8088 → port 80 internal)."""
    return os.environ.get("WIDGET_BUNDLE_BASE", "http://localhost:8088").rstrip("/")


def _api_base() -> str:
    return os.environ.get("PUBLIC_API_BASE", "http://localhost:8000").rstrip("/")


def _frame_ancestors(origins: tuple[str, ...]) -> str:
    """Build the CSP `frame-ancestors` directive.

    Empty allowlist → `'none'` (blocks all framing — explicit refusal). Each
    origin is rendered verbatim; admin must include scheme + host + port.
    """
    if not origins:
        return "frame-ancestors 'none'"
    return "frame-ancestors " + " ".join(origins)


def _embed_html(widget_id: str, public_cfg: WidgetPublicConfig) -> str:
    """Minimal HTML shell loaded inside the iframe. Links the widget
    stylesheet, sets two globals the bundle reads at bootstrap, and pulls the
    JS bundle — both static assets come from the `widget` host.

    The Vite *library* build emits the widget's CSS as a separate `style.css`
    (it is not inlined into the IIFE), so the stylesheet must be linked
    explicitly here or the widget renders unstyled. The `<link>` precedes the
    inline `<style>` so the per-config `--mc-primary` override still wins.

    No inline event handlers; only one inline `<script>` declaring the
    globals (acceptable under the page's own CSP — the parent page's CSP is
    what we enforce via `frame-ancestors`)."""
    bundle = _bundle_base()
    api = _api_base()
    color = public_cfg.primary_color.replace('"', "")
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{public_cfg.name}</title>"
        f'<link rel="stylesheet" href="{bundle}/style.css">'
        f"<style>:root{{color-scheme:dark;--mc-primary:{color};}}"
        "html,body,#root{margin:0;padding:0;height:100%;background:transparent;}"
        "</style>"
        "</head><body>"
        '<div id="root"></div>'
        "<script>"
        f"window.__MC_WIDGET_ID__={widget_id!r};"
        f"window.__MC_API_BASE__={api!r};"
        "</script>"
        f'<script src="{bundle}/widget-bundle.js"></script>'
        "</body></html>"
    )


@router.get("/{widget_id}/config")
async def get_public_config(widget_id: str) -> JSONResponse:
    try:
        cfg = await widget_svc().get(widget_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    public = WidgetPublicConfig.from_config(cfg)
    return JSONResponse(
        {
            "id": public.id,
            "name": public.name,
            "primary_color": public.primary_color,
            "position": public.position,
            "greeting_text": public.greeting_text,
            "enabled_tools": list(public.enabled_tools),
            # Resolved palette — the widget bundle applies these verbatim.
            "theme": resolve_theme(public.theme),
        }
    )


@router.post("/{widget_id}/session", status_code=status.HTTP_201_CREATED)
async def mint_session(widget_id: str) -> dict:
    try:
        minted = await anon_svc().mint(widget_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return {
        "token": minted.token,
        "token_type": "bearer",
        "widget_session_id": minted.widget_session_id,
        "widget_id": minted.widget_id,
        "enabled_tools": list(minted.enabled_tools),
        "expires_in": minted.expires_in,
    }


@router.get("/{widget_id}/embed")
async def embed_iframe(widget_id: str, request: Request) -> HTMLResponse:
    try:
        cfg = await widget_svc().get(widget_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc

    public = WidgetPublicConfig.from_config(cfg)
    html = _embed_html(widget_id, public)
    headers = {
        "Content-Security-Policy": _frame_ancestors(cfg.allowed_origins),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    return HTMLResponse(content=html, headers=headers)
