"""Tracing span tree assertions — uses the tracing port's `_set_test_recorder`
hook so CI stays offline (no real Langfuse container). The hook records every
@observe call as a stack-aware context manager.

For the live-Langfuse smoke (mandated demo artifact), see RUNBOOK.md
"Verifying trace tree after bootstrap".
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import current_principal
from app.api.chat import router as chat_router
from app.domain.exceptions import ClassifierUnavailable
from app.infra import tracing


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


class _Recorder:
    def __init__(self):
        self.spans: list[dict] = []
        self.stack: list[dict] = []

    def __call__(self, name: str, as_type: str | None):
        rec = self
        record = {"name": name, "as_type": as_type, "depth": len(rec.stack)}

        class _Ctx:
            def __enter__(self_inner):
                rec.spans.append(record)
                rec.stack.append(record)
                return record

            def __exit__(self_inner, *exc):
                rec.stack.pop()
                return False

        return _Ctx()


@pytest.fixture
def recording_client(monkeypatch) -> tuple[TestClient, _Recorder]:
    recorder = _Recorder()
    tracing._set_test_recorder(recorder)

    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr("app.services.chatbot._recall", _no_recall)

    yield TestClient(_build_app()), recorder

    tracing._set_test_recorder(None)


def _make_tool_then_hedge_stream(tool_name: str, tool_args: dict):
    """Fake LLM stream — decorated with @observe so the recorder sees it as a
    generation span (just like the real stream_chat_with_tools)."""
    state = {"call": 0}

    @tracing.observe(as_type="generation", name="llm.stream_chat_with_tools")
    async def stream(messages: list[dict], tools=None, temperature=0.2) -> AsyncIterator[dict]:
        state["call"] += 1
        if state["call"] == 1:
            yield {
                "type": "stream_end",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": tool_name,
                        "arguments": json.dumps(tool_args),
                    }
                ],
            }
            return
        for tok in ["Sorry", " ", "couldn't"]:
            yield {"type": "token", "content": tok}
        yield {"type": "stream_end", "finish_reason": "stop", "tool_calls": []}

    return stream


def _drain(r):
    for _ in r.iter_lines():
        pass


def test_happy_path_span_tree(recording_client, monkeypatch):
    client, recorder = recording_client

    @tracing.observe(as_type="generation", name="llm.stream_chat_with_tools")
    async def _direct_answer(messages, tools=None, temperature=0.2):
        yield {"type": "token", "content": "ok"}
        yield {"type": "stream_end", "finish_reason": "stop", "tool_calls": []}

    monkeypatch.setattr("app.services.chatbot.stream_chat_with_tools", _direct_answer)

    with client.stream("POST", "/api/chat", json={"message": "hi"}) as r:
        _drain(r)

    names = [s["name"] for s in recorder.spans]
    assert "chat_turn" in names
    assert "llm.stream_chat_with_tools" in names

    chat_turn = next(s for s in recorder.spans if s["name"] == "chat_turn")
    llm = next(s for s in recorder.spans if s["name"] == "llm.stream_chat_with_tools")
    assert chat_turn["depth"] == 0
    assert llm["depth"] > chat_turn["depth"]
    assert llm["as_type"] == "generation"


def test_error_path_trace_has_failed_tool_and_hedge(recording_client, monkeypatch):
    """Slice 12 mandated artifact: one chat turn yields a trace tree with the
    failed tool span AND the hedge generation span — same scenario as the
    slice 09 tool-failure recovery test, observed at the tracing layer."""
    client, recorder = recording_client

    monkeypatch.setattr(
        "app.services.chatbot.stream_chat_with_tools",
        _make_tool_then_hedge_stream("classify_issue", {"text": "boom"}),
    )

    def _raise(self, text):
        raise ClassifierUnavailable("model-server down")

    monkeypatch.setattr("app.infra.model_server_client.ModelServerClient.classify", _raise)

    with client.stream("POST", "/api/chat", json={"message": "classify this"}) as r:
        _drain(r)

    names = [s["name"] for s in recorder.spans]
    assert "chat_turn" in names
    assert names.count("llm.stream_chat_with_tools") >= 2  # failed call + hedge
    assert "tool.classify_issue" in names
    tool_span = next(s for s in recorder.spans if s["name"] == "tool.classify_issue")
    assert tool_span["as_type"] == "tool"
    # Tool was called between the two LLM calls
    tool_idx = names.index("tool.classify_issue")
    llm_indices = [i for i, n in enumerate(names) if n == "llm.stream_chat_with_tools"]
    assert llm_indices[0] < tool_idx < llm_indices[1]
