"""End-to-end auth flow against the real /auth routes.

Skipped if Postgres isn't reachable. Exercises:
- POST /auth/register
- POST /auth/jwt/login
- GET  /api/me
- POST /auth/jwt/revoke

Uses httpx.AsyncClient + ASGITransport because Windows' proactor event loop
hits an `AttributeError: 'NoneType' object has no attribute 'send'` when
FastAPI's TestClient (sync) re-enters asyncpg connections.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def auth_app(pg_available, monkeypatch):
    if not pg_available:
        pytest.skip("Postgres not reachable.")

    from app.infra import vault as vault_module

    vault = vault_module.get_vault()
    monkeypatch.setattr(
        vault,
        "_cache",
        {
            "shared/jwt": {"signing_key": "test-signing-key-not-for-prod-32b!"},
            "api/db": {"url": "postgresql+asyncpg://copilot@localhost:5432/copilot"},
            "api/redis": {"url": "redis://localhost:6379/15"},
            "api/llm": {"groq_api_key": "placeholder"},
            "api/tracing": {
                "langfuse_public_key": "placeholder",
                "langfuse_secret_key": "placeholder",
                "langfuse_host": "http://localhost:3000",
            },
            "api/blob": {
                "endpoint": "localhost:9000",
                "access_key": "minioadmin",
                "secret_key": "minioadmin",
                "bucket": "mc-evals",
            },
            "streamlit/api": {"api_base": "http://localhost:8000"},
        },
    )

    from app.infra import sa_db

    # Dispose any engine left by a prior test before swapping the singleton.
    if sa_db._engine is not None:
        await sa_db._engine.dispose()
    sa_db._engine = None
    sa_db._sessionmaker = None

    from fastapi import FastAPI

    from app.api.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    try:
        yield app
    finally:
        if sa_db._engine is not None:
            await sa_db._engine.dispose()
        sa_db._engine = None
        sa_db._sessionmaker = None


@pytest_asyncio.fixture
async def client(auth_app):
    transport = ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_register_login_me(client):
    email = f"test-{uuid.uuid4().hex[:8]}@example.com"
    pw = "supersecret-test-pw"

    r = await client.post("/auth/register", json={"email": email, "password": pw})
    assert r.status_code in (200, 201), r.text
    user = r.json()
    assert user["email"] == email
    assert user["role"] == "user"

    r = await client.post("/auth/jwt/login", data={"username": email, "password": pw})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    assert token

    r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["kind"] == "user"
    assert me["email"] == email
    assert me["role"] == "user"


async def test_me_rejects_missing_or_bogus_token(client):
    r = await client.get("/api/me")
    assert r.status_code == 401

    r = await client.get("/api/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_revoke_returns_ok_without_jti(client):
    """fastapi-users' default JWT strategy doesn't emit a jti claim.

    Forge a token directly with the test signing key so this assertion
    doesn't go through the SA-backed register/login path (keeps the test
    independent of the engine state created by earlier tests).
    """
    import jwt as pyjwt

    token = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "fastapi-users:auth",
            "exp": 9999999999,
            # no jti by design
        },
        "test-signing-key-not-for-prod-32b!",
        algorithm="HS256",
    )

    r = await client.post("/auth/jwt/revoke", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["reason"] in {"no_jti", "redis_unavailable"}
