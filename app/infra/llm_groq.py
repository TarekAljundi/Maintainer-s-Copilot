"""Groq LLM adapter. Model: llama-3.3-70b-versatile. Streaming with tool-call support."""

from __future__ import annotations

from typing import Any, AsyncIterator

from groq import AsyncGroq

from app.domain.exceptions import LLMProviderError
from app.infra.vault import get_vault

MODEL = "llama-3.3-70b-versatile"


def _api_key() -> str:
    secrets = get_vault().cached("api/llm")
    key = secrets.get("groq_api_key") or ""
    if not key:
        raise LLMProviderError("groq_api_key missing from vault api/llm")
    return key


async def stream_chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events:

    - {"type": "token", "content": str}  for content deltas
    - {"type": "stream_end", "finish_reason": str, "tool_calls": list[dict]}
       at end (tool_calls is empty when finish_reason != 'tool_calls')
    """
    client = AsyncGroq(api_key=_api_key())
    kwargs: dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
    try:
        stream = await client.chat.completions.create(**kwargs)
        tool_calls_buf: dict[int, dict] = {}
        finish_reason: str | None = None
        async for chunk in stream:
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                yield {"type": "token", "content": delta.content}
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    buf = tool_calls_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        buf["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            buf["name"] = tc.function.name
                        if tc.function.arguments:
                            buf["arguments"] += tc.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason
        yield {
            "type": "stream_end",
            "finish_reason": finish_reason or "stop",
            "tool_calls": list(tool_calls_buf.values()),
        }
    except LLMProviderError:
        raise
    except Exception as exc:
        raise LLMProviderError(f"groq stream failed: {exc}") from exc


# Legacy thin wrapper used by older callers and tests. Slice 01 path.
async def stream_chat(user_msg: str, temperature: float = 0.2) -> AsyncIterator[str]:
    async for ev in stream_chat_with_tools(
        [{"role": "user", "content": user_msg}], tools=None, temperature=temperature
    ):
        if ev["type"] == "token":
            yield ev["content"]
