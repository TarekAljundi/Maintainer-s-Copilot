"""Loader script (/widget.js) basic contract.

The loader is hand-written JS templated by Python; we just assert the bits
that other tests + the demo depend on are present in the served body.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.loader import router as loader_router


def _build():
    app = FastAPI()
    app.include_router(loader_router)
    return TestClient(app)


def test_widget_js_served_with_js_content_type():
    r = _build().get("/widget.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")


def test_widget_js_reads_data_widget_id_and_fetches_config():
    body = _build().get("/widget.js").text
    assert "data-widget-id" in body
    assert "/widget/' + encodeURIComponent(widgetId) + '/config" in body
    assert "/widget/' + encodeURIComponent(widgetId) + '/embed" in body


def test_widget_js_postmessage_protocol_present():
    body = _build().get("/widget.js").text
    assert "mc:resize" in body
    assert "mc:ready" in body
    assert "mc:theme" in body
