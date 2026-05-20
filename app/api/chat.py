"""Chat endpoint. SSE streaming. Delegates to ChatbotService.

PRD §Authentication: user_id is derived from the JWT principal, no longer
accepted in the request body. Widget sessions are accepted as principals
but get no episodic memory (slice 11 plan §D).
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.auth import current_principal, principal_user_id
from app.services.chatbot import run_turn

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


async def _sse(req: ChatRequest, user_id: str | None) -> AsyncIterator[bytes]:
    async for event in run_turn(req.message, req.conversation_id, user_id=user_id):
        yield f"data: {json.dumps(event)}\n\n".encode()
    yield b"data: [DONE]\n\n"


@router.post("")
async def chat(
    req: ChatRequest,
    principal=Depends(current_principal),
) -> StreamingResponse:
    user_id = principal_user_id(principal)
    return StreamingResponse(_sse(req, user_id), media_type="text/event-stream")
