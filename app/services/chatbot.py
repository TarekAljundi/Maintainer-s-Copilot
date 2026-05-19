"""ChatbotService — agent loop, tool dispatch, mem inject, SSE event stream.

Interface:
    run_turn(conv_id, user_msg, user) -> AsyncIterator[Event]

Slice 01: thin passthrough — stream Groq tokens, no tools/memory yet.
Future slices add: agent loop (6-step cap, temp=0.2), tool dispatch, memory recall.
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncIterator

from app.infra.llm_groq import stream_chat

Event = dict[str, Any]


async def run_turn(user_msg: str, conversation_id: str | None = None) -> AsyncIterator[Event]:
    msg_id = uuid.uuid4().hex
    async for token in stream_chat(user_msg):
        yield {"type": "token", "content": token}
    yield {"type": "done", "msg_id": msg_id}
