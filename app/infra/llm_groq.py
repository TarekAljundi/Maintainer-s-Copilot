"""LLM adapter — OpenAI-compatible streaming with tool-call support.

PRD §Further Notes: stack was originally unified on Groq llama-3.3-70b-versatile.
This module now routes between Groq and OpenRouter (NVIDIA Nemotron 3 Super) via
the `LLM_PROVIDER` env var to absorb Groq's TPD limit without re-architecting
the chatbot. Both providers expose OpenAI-compatible APIs, so the streaming +
tool_calls parser is identical — only the client base_url + API key + default
model differ.

Provider matrix:
    groq        (default)      llama-3.3-70b-versatile @ https://api.groq.com/openai/v1
    openrouter                 nvidia/nemotron-3-super-120b-a12b:free @ https://openrouter.ai/api/v1

Vault path `api/llm` carries both `groq_api_key` and `openrouter_api_key`.

The module's filename stays `llm_groq.py` to avoid churning import sites; the
public entry point `stream_chat_with_tools` keeps the same signature.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from app.domain.exceptions import LLMProviderError
from app.infra.vault import get_vault


@dataclass(frozen=True, slots=True)
class _ProviderConfig:
    name: str
    base_url: str
    api_key_field: str
    default_model: str


_PROVIDERS: dict[str, _ProviderConfig] = {
    "groq": _ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_field="groq_api_key",
        default_model="llama-3.3-70b-versatile",
    ),
    "openrouter": _ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_field="openrouter_api_key",
        default_model="nvidia/nemotron-3-super-120b-a12b:free",
    ),
}


def _provider() -> _ProviderConfig:
    name = os.environ.get("LLM_PROVIDER", "groq").strip().lower()
    cfg = _PROVIDERS.get(name)
    if cfg is None:
        raise LLMProviderError(
            f"unknown LLM_PROVIDER={name!r}; expected one of {list(_PROVIDERS)}"
        )
    return cfg


def _api_key(cfg: _ProviderConfig) -> str:
    secrets = get_vault().cached("api/llm")
    key = secrets.get(cfg.api_key_field) or ""
    if not key or key == "placeholder":
        raise LLMProviderError(
            f"vault api/llm.{cfg.api_key_field} missing or placeholder "
            f"for LLM_PROVIDER={cfg.name}"
        )
    return key


def _model() -> str:
    # Allow per-deployment override via env without touching code.
    return os.environ.get("LLM_MODEL") or _provider().default_model


MODEL = _model()  # for backwards-compat reads in callers that import the constant


async def stream_chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    temperature: float = 0.2,
) -> AsyncIterator[dict[str, Any]]:
    """Yield events:

    - {"type": "token", "content": str}  for content deltas
    - {"type": "stream_end", "finish_reason": str, "tool_calls": list[dict]}
       at end (tool_calls is empty when finish_reason != 'tool_calls')

    Same shape regardless of provider — both Groq and OpenRouter emit
    OpenAI-compatible streaming chunks (`choices[0].delta.content` /
    `delta.tool_calls[].function.{name,arguments}` deltas).
    """
    cfg = _provider()
    client = AsyncOpenAI(api_key=_api_key(cfg), base_url=cfg.base_url)

    kwargs: dict[str, Any] = {
        "model": _model(),
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
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is None:
                continue
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
        raise LLMProviderError(f"{cfg.name} stream failed: {exc}") from exc


# Legacy thin wrapper used by older callers and tests. Slice 01 path.
async def stream_chat(user_msg: str, temperature: float = 0.2) -> AsyncIterator[str]:
    async for ev in stream_chat_with_tools(
        [{"role": "user", "content": user_msg}], tools=None, temperature=temperature
    ):
        if ev["type"] == "token":
            yield ev["content"]
