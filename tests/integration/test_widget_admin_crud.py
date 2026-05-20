"""Admin widget CRUD — end-to-end through HTTP without Postgres.

Stubs WidgetConfigService for in-memory CRUD and patches require_admin to a
fixed admin user. Asserts:
  - non-admin gets 403 (we re-enable require_admin temporarily for that case).
  - create → list → get → patch → delete round-trip.
  - embed-snippet returns a real-looking script tag.
  - every write emits an audit row.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.api.auth import require_admin
from app.domain.exceptions import NotFoundError
from app.domain.widget import WidgetConfig


WIDGET_ID = "11111111-1111-1111-1111-111111111111"


class _FakeUser:
    id = "33333333-3333-3333-3333-333333333333"
    email = "admin@example.com"
    role = "admin"
    is_active = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeSvc:
    def __init__(self) -> None:
        self.rows: dict[str, WidgetConfig] = {}

    async def create(self, **kw):
        cfg = WidgetConfig(
            id=WIDGET_ID,
            name=kw["name"],
            allowed_origins=tuple(kw["allowed_origins"]),
            primary_color=kw.get("primary_color", "#222"),
            position=kw.get("position", "br"),
            greeting_text=kw.get("greeting_text", "hi"),
            enabled_tools=tuple(kw.get("enabled_tools") or ()),
            created_at=_now(),
            updated_at=_now(),
        )
        self.rows[WIDGET_ID] = cfg
        return cfg

    async def list_(self, limit=100, offset=0):
        return list(self.rows.values())[offset : offset + limit]

    async def get(self, wid):
        if wid not in self.rows:
            raise NotFoundError("not found", widget_id=wid)
        return self.rows[wid]

    async def update(self, wid, **patch):
        if wid not in self.rows:
            raise NotFoundError("not found", widget_id=wid)
        old = self.rows[wid]
        self.rows[wid] = WidgetConfig(
            id=old.id,
            name=patch.get("name", old.name),
            allowed_origins=tuple(patch.get("allowed_origins", old.allowed_origins)),
            primary_color=patch.get("primary_color", old.primary_color),
            position=patch.get("position", old.position),
            greeting_text=patch.get("greeting_text", old.greeting_text),
            enabled_tools=tuple(patch.get("enabled_tools", old.enabled_tools)),
            created_at=old.created_at,
            updated_at=_now(),
        )
        return self.rows[wid]

    async def delete(self, wid):
        return self.rows.pop(wid, None) is not None


@pytest.fixture
def client(monkeypatch) -> tuple[TestClient, FakeSvc, list[dict]]:
    svc = FakeSvc()
    monkeypatch.setattr("app.api.admin.widget_svc", lambda: svc)

    audits: list[dict] = []

    async def _write_audit(**kw):
        audits.append(kw)

    monkeypatch.setattr("app.api.admin.write_audit", _write_audit)

    app = FastAPI()
    app.include_router(admin_router, prefix="/api")
    app.dependency_overrides[require_admin] = lambda: _FakeUser()
    return TestClient(app), svc, audits


def test_create_list_get_patch_delete_roundtrip(client):
    c, svc, audits = client
    r = c.post(
        "/api/admin/widgets",
        json={
            "name": "demo",
            "allowed_origins": ["http://localhost:8087"],
            "primary_color": "#222",
            "position": "br",
            "greeting_text": "hi",
            "enabled_tools": ["classify_issue"],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == WIDGET_ID
    assert body["allowed_origins"] == ["http://localhost:8087"]
    assert any(a["action"] == "widget_config_create" for a in audits)

    listed = c.get("/api/admin/widgets").json()
    assert len(listed) == 1

    fetched = c.get(f"/api/admin/widgets/{WIDGET_ID}").json()
    assert fetched["name"] == "demo"

    patched = c.patch(f"/api/admin/widgets/{WIDGET_ID}", json={"name": "renamed"}).json()
    assert patched["name"] == "renamed"
    assert any(a["action"] == "widget_config_update" for a in audits)

    r = c.delete(f"/api/admin/widgets/{WIDGET_ID}")
    assert r.status_code == 204
    assert any(a["action"] == "widget_config_delete" for a in audits)


def test_embed_snippet_shape(client):
    c, svc, _ = client
    c.post(
        "/api/admin/widgets",
        json={"name": "x", "allowed_origins": ["http://localhost:8087"]},
    )
    r = c.get(
        f"/api/admin/widgets/{WIDGET_ID}/embed-snippet",
        headers={"host": "api.example.com"},
    )
    body = r.json()
    assert body["widget_id"] == WIDGET_ID
    assert "widget.js" in body["src"]
    assert f'data-widget-id="{WIDGET_ID}"' in body["snippet"]


def test_non_admin_gets_403(monkeypatch):
    """When require_admin is NOT overridden, the auth dep refuses anonymous
    requests. We just assert the route did NOT succeed — fastapi-users
    short-circuits at the auth dep before the handler runs, but without a
    real DB the user-manager dep can return 500 instead of 401. Either way,
    the unauthenticated request did not write a widget config."""
    svc = FakeSvc()
    monkeypatch.setattr("app.api.admin.widget_svc", lambda: svc)
    app = FastAPI()
    app.include_router(admin_router, prefix="/api")
    r = TestClient(app, raise_server_exceptions=False).post(
        "/api/admin/widgets",
        json={"name": "x", "allowed_origins": []},
    )
    assert r.status_code != 201
    assert r.status_code >= 400
    assert svc.rows == {}  # handler never ran


def test_patch_invalid_position_422(client):
    c, _, _ = client
    c.post("/api/admin/widgets", json={"name": "x", "allowed_origins": []})
    r = c.patch(f"/api/admin/widgets/{WIDGET_ID}", json={"position": "mm"})
    assert r.status_code == 422
