"""Single exception handler. AppError → structured JSON envelope.

PRD Q23: envelope is {code, message, request_id, trace_id, extras}. Users
never see a stack trace; uncaught exceptions become a generic 500 with the
same envelope shape and a server-side log line.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.exceptions import AppError
from app.infra import tracing
from app.infra.logging import bind_request_id, bind_trace_id, get_logger

log = get_logger(__name__)


def _envelope(
    code: str, message: str, request_id: str | None, trace_id: str | None, extras: Any
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "request_id": request_id,
        "trace_id": trace_id,
        "extras": extras or {},
    }


def _request_id(request: Request) -> str | None:
    rid = request.headers.get("x-request-id")
    return rid or getattr(request.state, "request_id", None)


def register(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        rid = _request_id(request)
        tid = tracing.trace_id()
        log.warning(
            "app_error",
            code=exc.code,
            status_code=exc.status_code,
            message=exc.message,
            request_id=rid,
            trace_id=tid,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, rid, tid, exc.extras),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = _request_id(request)
        tid = tracing.trace_id()
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "invalid_input",
                "request validation failed",
                rid,
                tid,
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        tid = tracing.trace_id()
        log.exception(
            "unhandled_exception",
            exc_type=type(exc).__name__,
            request_id=rid,
            trace_id=tid,
        )
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "internal server error", rid, tid, {}),
        )


__all__ = ["register", "bind_request_id", "bind_trace_id"]
