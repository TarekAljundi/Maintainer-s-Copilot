"""Chat SSE integration test. Groq client is monkey-patched; lifespan is bypassed."""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router as chat_router


async def _fake_stream(user_msg: str, temperature: float = 0.2) -> AsyncIterator[str]:
    for tok in ["Hello", " ", "world"]:
        yield tok


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr("app.services.chatbot.stream_chat", _fake_stream)
    # Bypass lifespan (vault not running in unit env).
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    return TestClient(app)


def test_sse_emits_tokens_done_and_terminator(client: TestClient):
    payload = {"user_id": "u1", "message": "hi"}
    with client.stream("POST", "/api/chat", json=payload) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events: list = []
        terminator_seen = False
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                terminator_seen = True
                continue
            events.append(json.loads(data))

    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["Hello", " ", "world"]
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and "msg_id" in done[0]
    assert terminator_seen
