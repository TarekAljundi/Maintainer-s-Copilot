"""Dynamic CORS middleware: per-widget allowlist sourced from WidgetConfigService.

Builds an isolated app with the middleware mounted; stubs the service's
allowed_origins(). No Postgres.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.dynamic_cors import DynamicCORSMiddleware


WIDGET_ID = "11111111-1111-1111-1111-111111111111"


def _app(monkeypatch, allowed: tuple[str, ...]) -> TestClient:
    class _Svc:
        async def allowed_origins(self, wid: str):
            assert wid == WIDGET_ID
            return allowed

    monkeypatch.setattr("app.services.widget_config.default_service", lambda: _Svc())

    app = FastAPI()
    app.add_middleware(DynamicCORSMiddleware)

    @app.get("/widget/{wid}/config")
    async def _config(wid: str):
        return JSONResponse({"id": wid})

    return TestClient(app)


def test_matched_origin_emits_cors_headers(monkeypatch):
    c = _app(monkeypatch, ("http://localhost:8087",))
    r = c.get(f"/widget/{WIDGET_ID}/config", headers={"origin": "http://localhost:8087"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:8087"
    assert "Origin" in r.headers["vary"]


def test_unmatched_origin_no_cors_headers(monkeypatch):
    c = _app(monkeypatch, ("http://localhost:8087",))
    r = c.get(f"/widget/{WIDGET_ID}/config", headers={"origin": "http://attacker.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_preflight_options_returns_204_with_cors(monkeypatch):
    c = _app(monkeypatch, ("http://localhost:8087",))
    r = c.options(
        f"/widget/{WIDGET_ID}/config",
        headers={
            "origin": "http://localhost:8087",
            "access-control-request-method": "GET",
            "access-control-request-headers": "Authorization",
        },
    )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "http://localhost:8087"
    assert "Authorization" in r.headers["access-control-allow-headers"]


def test_no_origin_header_is_passthrough(monkeypatch):
    c = _app(monkeypatch, ("http://localhost:8087",))
    r = c.get(f"/widget/{WIDGET_ID}/config")
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
