"""ChatbotService — agent loop, tool dispatch, mem inject, SSE event stream.

Interface:
    run_turn(conv_id, user_msg, user) -> AsyncIterator[Event]

Slice 03: single-shot tool dispatch (one round of tool calls, then final stream).
Full 6-step loop with retry-on-tool-error lands in slice 09. Memory recall
injection lands in slice 11.
"""

from __future__ import annotations

import inspect
import json
import uuid
from typing import Any, AsyncIterator

from app.domain.tools import TOOL_DISPATCH, TOOL_SCHEMAS
from app.infra.llm_groq import stream_chat_with_tools

Event = dict[str, Any]

SYSTEM_PROMPT = (
    "You are Maintainer's Copilot, an assistant for an open-source project maintainer. "
    "Use the provided tools when they directly fit the user's request. "
    "When a tool returns a result, you MUST quote the exact field values from the result "
    "(label, confidence, ...) verbatim. Never substitute, paraphrase, or invent values. "
    "If the tool returned label='question', you write 'question' — not any other word. "
    "When search_knowledge returns passages, ground your answer in those passages and cite "
    "each fact using the result's `citation` field verbatim (e.g. 'User Guide > IO > CSV' "
    "or '#61809'). Do not invent breadcrumbs, section names, or issue numbers."
)


async def run_turn(user_msg: str, conversation_id: str | None = None) -> AsyncIterator[Event]:
    msg_id = uuid.uuid4().hex
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    finish_reason: str | None = None
    pending_tool_calls: list[dict] = []

    async for ev in stream_chat_with_tools(messages, tools=TOOL_SCHEMAS):
        if ev["type"] == "token":
            yield {"type": "token", "content": ev["content"]}
        elif ev["type"] == "stream_end":
            finish_reason = ev["finish_reason"]
            pending_tool_calls = ev["tool_calls"]

    if finish_reason == "tool_calls" and pending_tool_calls:
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in pending_tool_calls
                ],
            }
        )
        for tc in pending_tool_calls:
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call_start", "name": tc["name"], "args": args}
            handler = TOOL_DISPATCH.get(tc["name"])
            if handler is None:
                result = {"ok": False, "error": "tool_not_registered", "detail": tc["name"]}
            else:
                value = handler(**args)
                result = await value if inspect.isawaitable(value) else value
            yield {"type": "tool_call_result", "name": tc["name"], "result": result}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result),
                }
            )

        # Second round: no tools — model must produce a chat response.
        async for ev in stream_chat_with_tools(messages, tools=None):
            if ev["type"] == "token":
                yield {"type": "token", "content": ev["content"]}

    yield {"type": "done", "msg_id": msg_id}
