"""Mid-stream LLM error → SSE `error` event + clean `[DONE]`.

Repro of the slice-11 smoke bug: Groq 429 raised inside the streaming body
caused an httpx.RemoteProtocolError on the client. Slice 09 must catch it in
the orchestrator and emit a structured error event before the terminator.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import current_principal
from app.api.chat import router as chat_router
from app.domain.exceptions import LLMProviderError


class _FakeUser:
    def __init__(self) -> None:
        self.id = "11111111-1111-1111-1111-111111111111"
        self.email = "test@example.com"
        self.role = "user"
        self.is_active = True


async def _fake_principal() -> _FakeUser:
    return _FakeUser()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    app.dependency_overrides[current_principal] = _fake_principal
    return app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    async def _broken_stream(
        messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2
    ) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "Hi"}
        yield {"type": "token", "content": " there"}
        raise LLMProviderError("groq 429 — TPD exceeded")

    monkeypatch.setattr("app.services.chatbot.stream_chat_with_tools", _broken_stream)

    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr("app.services.chatbot._recall", _no_recall)
    return TestClient(_build_app())


def test_mid_stream_llm_error_sse_envelope(client: TestClient):
    with client.stream("POST", "/api/chat", json={"message": "hi"}) as r:
        assert r.status_code == 200
        events = []
        terminator = False
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                terminator = True
                continue
            events.append(json.loads(data))

    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["Hi", " there"]
    err = [e for e in events if e["type"] == "error"]
    assert len(err) == 1
    assert err[0]["code"] == "llm_unavailable"
    assert "groq 429" in err[0]["message"]
    assert terminator  # connection ended cleanly
