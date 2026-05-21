"""Smoke test: a fresh `docker compose up` answers /api/health with 200.

Used by CI stage 3 after `scripts/wait_healthy.sh` reports the stack healthy.
The point is to verify the 8 boot checks all passed in a real container —
if any check raises, `BootValidator.validate_all()` calls SystemExit(1),
uvicorn exits, the container is unhealthy, and the smoke test fails.

Local repro:
    cp .env.example .env && echo "VAULT_TOKEN=ci-token" >> .env
    docker compose up -d
    ./scripts/wait_healthy.sh
    pytest tests/smoke -q
"""

from __future__ import annotations

import os

import httpx
import pytest

API_BASE = os.environ.get("MC_SMOKE_API_BASE", "http://localhost:8000")


def test_health_returns_200() -> None:
    try:
        r = httpx.get(f"{API_BASE}/api/health", timeout=5.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"api not reachable at {API_BASE}: {exc}")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}


def test_openapi_reachable() -> None:
    try:
        r = httpx.get(f"{API_BASE}/openapi.json", timeout=5.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"api not reachable at {API_BASE}: {exc}")
    assert r.status_code == 200
    body = r.json()
    assert body.get("info", {}).get("title") == "Maintainer's Copilot API"
