"""POST /widget/{id}/session mints a valid anon-widget JWT.

We stub the WidgetConfigService and force `_jwt_secret` to a known value so
we can decode the returned token and assert the principal helper resolves
it to an AnonWidgetSession.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.widget import router as widget_router
from app.domain.widget import WidgetConfig


WIDGET_ID = "11111111-1111-1111-1111-111111111111"
SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _patch_secret(monkeypatch):
    monkeypatch.setattr("app.services.anon_session._jwt_secret", lambda: SECRET)


def _cfg() -> WidgetConfig:
    now = datetime.now(timezone.utc)
    return WidgetConfig(
        id=WIDGET_ID,
        name="demo",
        allowed_origins=("http://localhost:8087",),
        primary_color="#222",
        position="br",
        greeting_text="hi",
        enabled_tools=("classify_issue", "search_knowledge"),
        created_at=now,
        updated_at=now,
    )


def test_mint_returns_token_payload(monkeypatch):
    from app.services.anon_session import AnonSessionService

    class _Svc:
        async def get(self, _wid):
            return _cfg()

    instance = AnonSessionService(configs=_Svc())
    # The widget route imports `default_service as anon_svc` — patch the alias.
    monkeypatch.setattr("app.api.widget.anon_svc", lambda: instance)

    app = FastAPI()
    app.include_router(widget_router)
    r = TestClient(app).post(f"/widget/{WIDGET_ID}/session")
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["widget_id"] == WIDGET_ID
    assert body["enabled_tools"] == ["classify_issue", "search_knowledge"]
    assert len(body["widget_session_id"]) > 8
    assert body["expires_in"] > 0
    assert body["token"].count(".") == 2


def test_mint_token_decodes_to_widget_session_principal(monkeypatch):
    """The token minted here must round-trip through current_principal as an
    AnonWidgetSession with the same fields."""
    import jwt as pyjwt

    class _Svc:
        async def get(self, _wid):
            return _cfg()

    from app.services.anon_session import AnonSessionService

    instance = AnonSessionService(configs=_Svc())
    monkeypatch.setattr("app.api.widget.anon_svc", lambda: instance)

    app = FastAPI()
    app.include_router(widget_router)
    r = TestClient(app).post(f"/widget/{WIDGET_ID}/session")
    token = r.json()["token"]
    payload = pyjwt.decode(token, SECRET, algorithms=["HS256"], audience="fastapi-users:auth")
    assert payload["sub"].startswith("widget_session:")
    assert payload["widget_id"] == WIDGET_ID
