"""GET /widget/{id}/embed returns the iframe shell with the right CSP header.

The allowed-origins list is sourced from the DB row via WidgetConfigService.
We stub the service entirely — no Postgres needed — and assert the header
content matches the configured allowlist exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.widget import router as widget_router
from app.domain.exceptions import NotFoundError
from app.domain.widget import WidgetConfig


WIDGET_ID = "11111111-1111-1111-1111-111111111111"


def _cfg(origins: tuple[str, ...]) -> WidgetConfig:
    now = datetime.now(timezone.utc)
    return WidgetConfig(
        id=WIDGET_ID,
        name="demo",
        allowed_origins=origins,
        primary_color="#222",
        position="br",
        greeting_text="hi",
        enabled_tools=("classify_issue",),
        created_at=now,
        updated_at=now,
    )


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(widget_router)
    return app


def test_embed_returns_csp_with_allowed_origins(monkeypatch):
    class _Svc:
        async def get(self, wid):
            assert wid == WIDGET_ID
            return _cfg(("http://localhost:8087", "https://example.com"))

    monkeypatch.setattr("app.api.widget.widget_svc", lambda: _Svc())
    r = TestClient(_build_app()).get(f"/widget/{WIDGET_ID}/embed")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp == "frame-ancestors http://localhost:8087 https://example.com"
    assert r.headers["cache-control"] == "no-store"


def test_embed_with_no_allowed_origins_emits_frame_ancestors_none(monkeypatch):
    class _Svc:
        async def get(self, wid):
            return _cfg(())

    monkeypatch.setattr("app.api.widget.widget_svc", lambda: _Svc())
    r = TestClient(_build_app()).get(f"/widget/{WIDGET_ID}/embed")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_embed_unknown_widget_returns_404(monkeypatch):
    class _Svc:
        async def get(self, _wid):
            raise NotFoundError("not found")

    monkeypatch.setattr("app.api.widget.widget_svc", lambda: _Svc())
    r = TestClient(_build_app(), raise_server_exceptions=False).get(
        "/widget/ffffffff-ffff-ffff-ffff-ffffffffffff/embed"
    )
    assert r.status_code == 404


def test_public_config_omits_allowed_origins(monkeypatch):
    class _Svc:
        async def get(self, _wid):
            return _cfg(("http://localhost:8087",))

    monkeypatch.setattr("app.api.widget.widget_svc", lambda: _Svc())
    r = TestClient(_build_app()).get(f"/widget/{WIDGET_ID}/config")
    assert r.status_code == 200
    body = r.json()
    assert "allowed_origins" not in body
    assert body["enabled_tools"] == ["classify_issue"]
    assert body["primary_color"] == "#222"
