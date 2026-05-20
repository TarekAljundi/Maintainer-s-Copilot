"""ASGI middlewares: request_id minting + structlog binding.

Mints a request_id if the client didn't send one, binds it to the structlog
ContextVar so every log line in this request carries it, echoes the same id
back on `x-request-id`.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.infra.logging import bind_request_id, bind_trace_id


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        bind_request_id(rid)
        bind_trace_id(None)
        try:
            response: Response = await call_next(request)
        finally:
            bind_request_id(None)
            bind_trace_id(None)
        response.headers["x-request-id"] = rid
        return response
