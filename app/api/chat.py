"""Chat endpoint. SSE streaming. Delegates to ChatbotService."""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.chatbot import run_turn

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    user_id: str
    message: str
    conversation_id: str | None = None


async def _sse(req: ChatRequest) -> AsyncIterator[bytes]:
    async for event in run_turn(req.message, req.conversation_id):
        yield f"data: {json.dumps(event)}\n\n".encode()
    yield b"data: [DONE]\n\n"


@router.post("")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_sse(req), media_type="text/event-stream")
