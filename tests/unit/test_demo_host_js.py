"""/demo/host.js — picks the most-recently-updated widget whose
allowed_origins contains the demo host URL; falls back to a warning script
when nothing matches.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.demo import router as demo_router
from app.domain.widget import WidgetConfig


def _cfg(wid: str, origins: tuple[str, ...], age_days: int) -> WidgetConfig:
    now = datetime.now(timezone.utc) - timedelta(days=age_days)
    return WidgetConfig(
        id=wid,
        name=f"w{wid[:4]}",
        allowed_origins=origins,
        primary_color="#000",
        position="br",
        greeting_text="hi",
        enabled_tools=("classify_issue",),
        created_at=now,
        updated_at=now,
    )


def _build(monkeypatch, widgets, env: dict[str, str] | None = None):
    class _Svc:
        async def list_(self, limit=100, offset=0):
            return widgets

    monkeypatch.setattr("app.api.demo.widget_svc", lambda: _Svc())
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    app = FastAPI()
    app.include_router(demo_router)
    return TestClient(app)


def test_picks_most_recent_widget_matching_demo_host(monkeypatch):
    older = _cfg("11111111-1111-1111-1111-111111111111", ("http://localhost:8087",), age_days=3)
    newer = _cfg("22222222-2222-2222-2222-222222222222", ("http://localhost:8087",), age_days=0)
    other = _cfg("33333333-3333-3333-3333-333333333333", ("https://example.com",), age_days=0)
    c = _build(monkeypatch, [older, newer, other])
    r = c.get("/demo/host.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    body = r.text
    assert newer.id in body
    assert older.id not in body
    assert other.id not in body
    assert "/widget.js" in body
    assert "data-widget-id" in body


def test_no_matching_widget_returns_warning_script(monkeypatch):
    other = _cfg("33333333-3333-3333-3333-333333333333", ("https://example.com",), age_days=0)
    c = _build(monkeypatch, [other])
    r = c.get("/demo/host.js")
    assert r.status_code == 200
    assert "no widget config" in r.text
    assert "Admin > Widgets" in r.text


def test_respects_DEMO_HOST_URL_env(monkeypatch):
    w = _cfg("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", ("https://myhost.example",), age_days=0)
    c = _build(monkeypatch, [w], env={"DEMO_HOST_URL": "https://myhost.example"})
    body = c.get("/demo/host.js").text
    assert w.id in body
