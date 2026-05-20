"""Chat endpoint. SSE streaming. Delegates to ChatbotService.

PRD §Authentication: user_id is derived from the JWT principal, no longer
accepted in the request body. Widget sessions are accepted as principals
but get no episodic memory (slice 11 plan §D).

Slice 09: defense-in-depth wrapper — if anything escapes `run_turn`, the
generator emits a final `{type:"error",...}` SSE event before `[DONE]` so
the browser EventSource closes cleanly instead of seeing a raw
RemoteProtocolError / IncompleteChunkedRead.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.auth import AnonWidgetSession, current_principal, principal_user_id
from app.domain.exceptions import AppError
from app.infra import tracing
from app.infra.logging import get_logger
from app.services.chatbot import run_turn

router = APIRouter(prefix="/chat", tags=["chat"])
log = get_logger(__name__)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


async def _sse(
    req: ChatRequest,
    user_id: str | None,
    widget_session_id: str | None = None,
) -> AsyncIterator[bytes]:
    try:
        async for event in run_turn(
            req.message,
            req.conversation_id,
            user_id=user_id,
            widget_session_id=widget_session_id,
        ):
            yield f"data: {json.dumps(event)}\n\n".encode()
    except AppError as exc:
        log.warning(
            "chat_stream_app_error", code=exc.code, message=exc.message, trace_id=tracing.trace_id()
        )
        err = {
            "type": "error",
            "code": exc.code,
            "message": exc.message or str(exc),
            "trace_id": tracing.trace_id(),
        }
        yield f"data: {json.dumps(err)}\n\n".encode()
    except Exception as exc:
        log.exception(
            "chat_stream_unhandled", exc_type=type(exc).__name__, trace_id=tracing.trace_id()
        )
        err = {
            "type": "error",
            "code": "internal_error",
            "message": str(exc) or "internal error",
            "trace_id": tracing.trace_id(),
        }
        yield f"data: {json.dumps(err)}\n\n".encode()
    yield b"data: [DONE]\n\n"


@router.post("")
async def chat(
    req: ChatRequest,
    principal=Depends(current_principal),
) -> StreamingResponse:
    user_id = principal_user_id(principal)
    widget_session_id = (
        principal.widget_session_id if isinstance(principal, AnonWidgetSession) else None
    )
    return StreamingResponse(
        _sse(req, user_id, widget_session_id=widget_session_id),
        media_type="text/event-stream",
    )
