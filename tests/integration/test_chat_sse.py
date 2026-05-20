"""Chat SSE integration test. Groq client + tool dispatch + auth dep are
monkey-patched."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import current_principal
from app.api.chat import router as chat_router


class _FakeUser:
    """Minimal stand-in for fastapi-users User in tests."""

    def __init__(self, uid: str = "11111111-1111-1111-1111-111111111111") -> None:
        self.id = uid
        self.email = "test@example.com"
        self.role = "user"
        self.is_active = True


async def _fake_principal() -> _FakeUser:
    return _FakeUser()


async def _fake_stream_no_tool(
    messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2
) -> AsyncIterator[dict[str, Any]]:
    """LLM answers directly, no tool call."""
    for tok in ["Hello", " ", "world"]:
        yield {"type": "token", "content": tok}
    yield {"type": "stream_end", "finish_reason": "stop", "tool_calls": []}


def _make_fake_stream_with_tool():
    """First call returns a tool_call; second call streams a final answer."""
    state = {"call": 0}

    async def stream(messages, tools=None, temperature=0.2):
        state["call"] += 1
        if state["call"] == 1:
            yield {
                "type": "stream_end",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "classify_issue",
                        "arguments": json.dumps({"text": "boom 500 on auth"}),
                    }
                ],
            }
        else:
            for tok in ["Looks", " ", "like", " ", "bug"]:
                yield {"type": "token", "content": tok}
            yield {"type": "stream_end", "finish_reason": "stop", "tool_calls": []}

    return stream


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    app.dependency_overrides[current_principal] = _fake_principal
    return app


@pytest.fixture
def client_no_tool(monkeypatch) -> TestClient:
    monkeypatch.setattr("app.services.chatbot.stream_chat_with_tools", _fake_stream_no_tool)
    # Stub out memory recall so test doesn't need a real Postgres.
    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr("app.services.chatbot._recall", _no_recall)
    return TestClient(_build_app())


@pytest.fixture
def client_with_tool(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "app.services.chatbot.stream_chat_with_tools", _make_fake_stream_with_tool()
    )

    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr("app.services.chatbot._recall", _no_recall)
    monkeypatch.setitem(
        __import__("app.domain.tools", fromlist=["TOOL_DISPATCH"]).TOOL_DISPATCH,
        "classify_issue",
        lambda **kw: {"ok": True, "label": "bug", "confidence": 0.91},
    )
    return TestClient(_build_app())


def _read_events(r) -> tuple[list[dict], bool]:
    events: list[dict] = []
    terminator_seen = False
    for line in r.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            terminator_seen = True
            continue
        events.append(json.loads(data))
    return events, terminator_seen


def test_sse_direct_answer_no_tool(client_no_tool: TestClient):
    payload = {"message": "hi"}
    with client_no_tool.stream("POST", "/api/chat", json=payload) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events, terminator = _read_events(r)
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert tokens == ["Hello", " ", "world"]
    assert [e for e in events if e["type"] == "tool_call_start"] == []
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1 and "msg_id" in done[0]
    assert terminator


def test_sse_dispatches_tool_then_streams_final(client_with_tool: TestClient):
    payload = {"message": "classify this issue"}
    with client_with_tool.stream("POST", "/api/chat", json=payload) as r:
        events, terminator = _read_events(r)
    starts = [e for e in events if e["type"] == "tool_call_start"]
    results = [e for e in events if e["type"] == "tool_call_result"]
    tokens = [e["content"] for e in events if e["type"] == "token"]
    assert len(starts) == 1 and starts[0]["name"] == "classify_issue"
    assert len(results) == 1 and results[0]["result"]["label"] == "bug"
    assert "".join(tokens) == "Looks like bug"
    assert terminator
