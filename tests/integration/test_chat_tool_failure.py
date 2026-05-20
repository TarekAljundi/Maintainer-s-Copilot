"""Tier-1 mandated tests: chat turn survives when a tool's upstream is down.

For each ToolFailure subclass — ClassifierUnavailable, RAGRetrievalFailure,
NERFailure — patch the upstream client to raise, send a chat turn that should
trigger that tool, and assert:
  - HTTP 200 (no 500 leaked).
  - Streamed events contain a `tool_call_result` with `ok=False`.
  - Final assistant message acknowledges the failure (any hedge token suffices).
  - The `[DONE]` terminator is emitted.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import current_principal
from app.api.chat import router as chat_router
from app.domain.exceptions import (
    ClassifierUnavailable,
    NERFailure,
    RAGRetrievalFailure,
)


class _FakeUser:
    def __init__(self, uid: str = "11111111-1111-1111-1111-111111111111") -> None:
        self.id = uid
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


def _make_tool_then_hedge_stream(tool_name: str, tool_args: dict):
    """First LLM call returns a tool_call → ok=False → second call hedges."""

    state = {"call": 0}

    async def stream(
        messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2
    ) -> AsyncIterator[dict]:
        state["call"] += 1
        if state["call"] == 1:
            yield {
                "type": "stream_end",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": f"call_{tool_name}",
                        "name": tool_name,
                        "arguments": json.dumps(tool_args),
                    }
                ],
            }
            return
        # Hedge: assistant acknowledges the tool failure
        for tok in ["Sorry", ",", " ", "I", " ", "couldn't", " ", "reach", " ", "the", " ", "tool"]:
            yield {"type": "token", "content": tok}
        yield {"type": "stream_end", "finish_reason": "stop", "tool_calls": []}

    return stream


def _read_events(r) -> tuple[list[dict[str, Any]], bool]:
    events: list[dict[str, Any]] = []
    terminator = False
    for line in r.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            terminator = True
            continue
        events.append(json.loads(data))
    return events, terminator


@pytest.fixture
def client(monkeypatch) -> TestClient:
    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr("app.services.chatbot._recall", _no_recall)
    return TestClient(_build_app())


def _assert_recovery(events: list[dict], terminator: bool, expected_code: str):
    results = [e for e in events if e["type"] == "tool_call_result"]
    tokens = [e["content"] for e in events if e["type"] == "token"]
    done = [e for e in events if e["type"] == "done"]
    errors = [e for e in events if e["type"] == "error"]

    assert errors == []  # tool failure must NOT become an api-level error
    assert len(results) == 1
    assert results[0]["result"]["ok"] is False
    assert results[0]["result"]["error"] == expected_code
    assert "".join(tokens), "hedge tokens should stream after the failed tool"
    assert any(word in "".join(tokens).lower() for word in ("sorry", "couldn't", "couldnt", "could not"))
    assert len(done) == 1
    assert terminator


def test_classifier_unavailable_recovers(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.services.chatbot.stream_chat_with_tools",
        _make_tool_then_hedge_stream("classify_issue", {"text": "boom 500 on auth"}),
    )

    def _raise(self, text):  # signature matches ModelServerClient.classify
        raise ClassifierUnavailable("model-server down")

    monkeypatch.setattr("app.infra.model_server_client.ModelServerClient.classify", _raise)

    with client.stream("POST", "/api/chat", json={"message": "classify this"}) as r:
        assert r.status_code == 200
        events, terminator = _read_events(r)
    _assert_recovery(events, terminator, expected_code="tool_failure.classifier")


def test_rag_retrieval_failure_recovers(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.services.chatbot.stream_chat_with_tools",
        _make_tool_then_hedge_stream("search_knowledge", {"query": "how do I groupby"}),
    )

    async def _raise(self, *a, **kw):
        raise RAGRetrievalFailure("pgvector unreachable")

    monkeypatch.setattr("app.services.rag.RAGService.retrieve", _raise)

    with client.stream("POST", "/api/chat", json={"message": "how do I groupby?"}) as r:
        assert r.status_code == 200
        events, terminator = _read_events(r)
    _assert_recovery(events, terminator, expected_code="tool_failure.rag")


def test_ner_failure_recovers(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "app.services.chatbot.stream_chat_with_tools",
        _make_tool_then_hedge_stream("extract_entities", {"text": "PR #61809 in core/io.py"}),
    )

    def _raise(self, text):
        raise NERFailure("spacy down")

    monkeypatch.setattr("app.infra.model_server_client.ModelServerClient.extract", _raise)

    with client.stream("POST", "/api/chat", json={"message": "extract entities"}) as r:
        assert r.status_code == 200
        events, terminator = _read_events(r)
    _assert_recovery(events, terminator, expected_code="tool_failure.ner")
