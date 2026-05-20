"""structlog config. Trace ID binding + redaction processor at boundary 1 of 3.

PRD §Observability §Redaction hookup at three boundaries.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

from app.infra.redaction import redact_obj


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def bind_request_id(rid: str | None) -> None:
    _request_id.set(rid)


def bind_trace_id(tid: str | None) -> None:
    _trace_id.set(tid)


def _inject_context(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    rid = _request_id.get()
    tid = _trace_id.get()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    if tid is not None:
        event_dict.setdefault("trace_id", tid)
    return event_dict


def _redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Boundary 1 — every event_dict is walked before emission."""
    return redact_obj(event_dict)


_configured = False


def configure(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level) if isinstance(level, str) else level
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    if not _configured:
        configure()
    return structlog.get_logger(name)
