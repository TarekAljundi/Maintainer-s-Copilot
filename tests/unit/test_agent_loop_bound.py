"""Agent loop iteration cap: 6 rounds → LLMProviderError("max_steps_exceeded").

The mid-stream-error → SSE-error-event polish is also asserted here so we don't
need a TestClient round-trip for both behaviors.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import pytest

from app.services import chatbot


def _make_always_tool_call_stream():
    """Fake LLM that always returns a tool_call → forces unbounded recursion."""

    async def stream(messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2):
        yield {
            "type": "stream_end",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_n",
                    "name": "classify_issue",
                    "arguments": json.dumps({"text": "x"}),
                }
            ],
        }

    return stream


@pytest.mark.asyncio
async def test_agent_loop_caps_at_six_iterations(monkeypatch):
    monkeypatch.setattr(chatbot, "stream_chat_with_tools", _make_always_tool_call_stream())

    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr(chatbot, "_recall", _no_recall)

    # Patch classify_issue tool to always return ok so the loop only stops
    # when the cap fires.
    from app.domain import tools as tools_mod

    monkeypatch.setitem(
        tools_mod.TOOL_DISPATCH, "classify_issue", lambda **kw: {"ok": True, "label": "bug"}
    )

    events: list[dict[str, Any]] = []
    async for ev in chatbot.run_turn("hi"):
        events.append(ev)

    err = [e for e in events if e["type"] == "error"]
    assert len(err) == 1
    assert err[0]["code"] == "llm_unavailable"
    assert "max_steps_exceeded" in err[0]["message"]
    assert any(e["type"] == "done" for e in events)
    # 6 iterations * 1 tool_call each
    assert len([e for e in events if e["type"] == "tool_call_start"]) == chatbot.MAX_AGENT_ITERATIONS


@pytest.mark.asyncio
async def test_mid_stream_llm_error_emits_error_event(monkeypatch):
    """LLMProviderError raised mid-stream is caught and surfaced as a single
    SSE-shaped error event; the iterator still emits `done` and never raises."""

    from app.domain.exceptions import LLMProviderError

    async def _broken_stream(
        messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2
    ) -> AsyncIterator[dict]:
        yield {"type": "token", "content": "Hi"}
        yield {"type": "token", "content": " there"}
        raise LLMProviderError("groq 429")

    monkeypatch.setattr(chatbot, "stream_chat_with_tools", _broken_stream)

    async def _no_recall(*_a, **_kw):
        return []

    monkeypatch.setattr(chatbot, "_recall", _no_recall)

    events = [ev async for ev in chatbot.run_turn("hi")]
    tokens = [e for e in events if e["type"] == "token"]
    errors = [e for e in events if e["type"] == "error"]
    done = [e for e in events if e["type"] == "done"]
    assert tokens == [{"type": "token", "content": "Hi"}, {"type": "token", "content": " there"}]
    assert len(errors) == 1
    assert errors[0]["code"] == "llm_unavailable"
    assert "groq 429" in errors[0]["message"]
    assert len(done) == 1
