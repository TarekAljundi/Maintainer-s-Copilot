"""Unit tests for the global exception handler envelope.

Asserts:
- AppError → status_code from the exception, body has {code,message,request_id,trace_id,extras}.
- RequestValidationError → 422 with shape.
- Uncaught Exception → 500 generic envelope, no traceback in body.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api import error_handlers
from app.api.middleware import RequestIDMiddleware
from app.domain.exceptions import LLMProviderError, NotFoundError


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    error_handlers.register(app)

    class _Body(BaseModel):
        n: int

    @app.get("/notfound")
    def _notfound():
        raise NotFoundError("missing thing", resource="widget", id="abc")

    @app.get("/infra")
    def _infra():
        raise LLMProviderError("groq down")

    @app.get("/boom")
    def _boom():
        raise RuntimeError("kaboom")  # noqa

    @app.post("/validate")
    def _validate(body: _Body):
        return {"ok": body.n}

    return app


def test_app_error_envelope():
    c = TestClient(_build_app(), raise_server_exceptions=False)
    r = c.get("/notfound")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "not_found"
    assert body["message"] == "missing thing"
    assert body["request_id"]  # middleware mints one
    assert body["trace_id"] is None  # tracing disabled in unit tests
    assert body["extras"] == {"resource": "widget", "id": "abc"}
    assert r.headers["x-request-id"] == body["request_id"]


def test_infra_error_503():
    c = TestClient(_build_app(), raise_server_exceptions=False)
    r = c.get("/infra")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "llm_unavailable"


def test_unhandled_500_no_traceback():
    c = TestClient(_build_app(), raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "internal_error"
    assert "kaboom" not in body["message"]  # never leak internals
    assert "Traceback" not in r.text


def test_request_validation_envelope():
    c = TestClient(_build_app(), raise_server_exceptions=False)
    r = c.post("/validate", json={"n": "not-an-int"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid_input"
    assert "errors" in body["extras"]


def test_inbound_request_id_preserved():
    c = TestClient(_build_app(), raise_server_exceptions=False)
    r = c.get("/notfound", headers={"x-request-id": "rid-123"})
    assert r.headers["x-request-id"] == "rid-123"
    assert r.json()["request_id"] == "rid-123"
