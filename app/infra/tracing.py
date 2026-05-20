"""TracingPort. Slice 08 wires the redaction `mask` at boundary 2; the full
Langfuse v2 SDK integration (spans, generations, tools, retrievals) lands in
slice 12. Until then `@observe` is a no-op decorator and `trace_id()` returns
None — but `mask()` is real and used by any future Langfuse handler.

Interface:
    @observe(as_type=...) decorator
    trace_id() -> str | None
    mask(obj) -> obj   # delegates to redactor before span send
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from app.infra.redaction import redact_obj


def mask(obj: Any) -> Any:
    """Boundary 2 — Langfuse `mask` callback. Identity-shaped, redaction-applied."""
    return redact_obj(obj)


def trace_id() -> str | None:
    """Returns the current trace ID. Wired in slice 12."""
    return None


def observe(as_type: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """No-op decorator placeholder; slice 12 swaps in the real Langfuse @observe.

    Preserves function signature so callers can decorate today without
    behavior change.
    """

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def _inner(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return _inner

    return _decorator
