"""Groq LLM adapter. Model: llama-3.3-70b-versatile. Streaming tokens."""

from __future__ import annotations

from typing import AsyncIterator

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


async def stream_chat(user_msg: str, temperature: float = 0.2) -> AsyncIterator[str]:
    """Yield content deltas from Groq for a single user turn."""
    client = AsyncGroq(api_key=_api_key())
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": user_msg}],
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield delta
    except LLMProviderError:
        raise
    except Exception as exc:
        raise LLMProviderError(f"groq stream failed: {exc}") from exc
