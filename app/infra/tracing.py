"""TracingPort. Langfuse v2 behind a typed interface so caller code can stay
ignorant of the SDK (PRD §Observability calls for swappable backends).

Interface:
    init_tracing() -> None              boot-time configure + auth-check
    @observe(as_type=..., name=...)     decorator (sync, coroutine, async-gen)
    trace_id() -> str | None            current trace id (from langfuse ctx)
    update_current_trace(**)            session_id, user_id, tags, ...
    update_current_observation(**)      span input/output/metadata/usage/model
    flush() -> None                     drain pending events (tests)
    mask(obj) -> obj                    redaction callback (boundary 2)

Span-type convention: Langfuse only knows "generation" natively; "tool",
"retrieval", "memory" land as default spans tagged with metadata.type for UI
filtering.

Disabled mode: when Vault keys are placeholder or `LANGFUSE_DISABLED=1`, every
entry point is a no-op so unit tests run without a Langfuse container.
"""

from __future__ import annotations

import inspect
import os
from functools import wraps
from typing import Any, Callable

from app.infra.redaction import redact_obj

_enabled = False
_initialized = False

# Test-only hook: when set to a (name, as_type) -> context-manager callable,
# every @observe-decorated function pushes a span into the recorder INSTEAD of
# calling Langfuse. This avoids importlib.reload gymnastics in tests.
_test_recorder: Callable[[str, str | None], Any] | None = None


def _set_test_recorder(recorder: Callable[[str, str | None], Any] | None) -> None:
    """Test-only: install a recording hook used by @observe. Pass None to clear."""
    global _test_recorder
    _test_recorder = recorder


def mask(obj: Any) -> Any:
    """Boundary 2 — Langfuse `mask` callback. Identity-shaped, redaction-applied."""
    return redact_obj(obj)


def _is_disabled_env() -> bool:
    return os.environ.get("LANGFUSE_DISABLED", "").strip().lower() in {"1", "true", "yes"}


def init_tracing() -> None:
    """Boot check #6: configure Langfuse + auth-check; raise on auth failure.

    Behavior:
      - placeholder/missing keys → disabled-mode (no error, no spans sent).
      - real keys + auth fail    → raises LLMProviderError (caller exits 1).
      - real keys + auth ok      → enabled.

    Idempotent: safe to call multiple times.
    """
    global _enabled, _initialized
    if _initialized:
        return
    _initialized = True

    if _is_disabled_env():
        _enabled = False
        return

    from app.domain.exceptions import LLMProviderError
    from app.infra.vault import get_vault

    try:
        secrets = get_vault().cached("api/tracing")
    except Exception as exc:
        raise LLMProviderError(f"tracing vault read failed: {exc}") from exc

    pub = (secrets.get("langfuse_public_key") or "").strip()
    sec = (secrets.get("langfuse_secret_key") or "").strip()
    host = (secrets.get("langfuse_host") or "http://langfuse:3000").strip()

    if not pub or not sec or pub == "placeholder" or sec == "placeholder":
        # Dev/test bootstrap: keys not yet provisioned. Don't fail boot — full
        # production bootstrap procedure is in RUNBOOK.md.
        _enabled = False
        return

    from langfuse.decorators import langfuse_context

    langfuse_context.configure(
        public_key=pub,
        secret_key=sec,
        host=host,
        mask=mask,
        enabled=True,
    )
    if not langfuse_context.auth_check():
        raise LLMProviderError("langfuse auth_check failed")
    _enabled = True


def _reset_for_tests() -> None:
    """Test-only hook to re-arm init_tracing()."""
    global _enabled, _initialized
    _enabled = False
    _initialized = False


def trace_id() -> str | None:
    if not _enabled:
        return None
    try:
        from langfuse.decorators import langfuse_context

        return langfuse_context.get_current_trace_id()
    except Exception:
        return None


def update_current_trace(**kwargs: Any) -> None:
    if not _enabled:
        return
    try:
        from langfuse.decorators import langfuse_context

        langfuse_context.update_current_trace(**kwargs)
    except Exception:
        pass


def update_current_observation(**kwargs: Any) -> None:
    if not _enabled:
        return
    try:
        from langfuse.decorators import langfuse_context

        langfuse_context.update_current_observation(**kwargs)
    except Exception:
        pass


def flush() -> None:
    if not _enabled:
        return
    try:
        from langfuse.decorators import langfuse_context

        langfuse_context.flush()
    except Exception:
        pass


def _tag_type(as_type: str | None) -> None:
    """Stamp metadata.type on the current observation for non-generation types."""
    if as_type and as_type != "generation":
        update_current_observation(metadata={"type": as_type})


def observe(
    as_type: str | None = None, name: str | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Tracing decorator. Handles sync, coroutine, and async-generator funcs.

    Langfuse only natively supports `as_type="generation"`; for "tool",
    "retrieval", "memory" we leave langfuse `as_type=None` (default span) and
    stamp `metadata={"type": <as_type>}` on the observation so the UI can
    filter by span kind.
    """
    lf_as_type = "generation" if as_type == "generation" else None

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        from langfuse.decorators import observe as lf_observe

        span_name = name or fn.__name__

        if inspect.isasyncgenfunction(fn):

            async def _tagged_gen(*args: Any, **kwargs: Any):
                _tag_type(as_type)
                async for item in fn(*args, **kwargs):
                    yield item

            lf_decorated = lf_observe(name=span_name, as_type=lf_as_type)(_tagged_gen)

            @wraps(fn)
            async def gen_wrapper(*args: Any, **kwargs: Any):
                if _test_recorder is not None:
                    with _test_recorder(span_name, as_type):
                        async for item in fn(*args, **kwargs):
                            yield item
                    return
                if not _enabled:
                    async for item in fn(*args, **kwargs):
                        yield item
                    return
                async for item in lf_decorated(*args, **kwargs):
                    yield item

            return gen_wrapper

        if inspect.iscoroutinefunction(fn):

            async def _tagged_coro(*args: Any, **kwargs: Any):
                _tag_type(as_type)
                return await fn(*args, **kwargs)

            lf_decorated = lf_observe(name=span_name, as_type=lf_as_type)(_tagged_coro)

            @wraps(fn)
            async def coro_wrapper(*args: Any, **kwargs: Any):
                if _test_recorder is not None:
                    with _test_recorder(span_name, as_type):
                        return await fn(*args, **kwargs)
                if not _enabled:
                    return await fn(*args, **kwargs)
                return await lf_decorated(*args, **kwargs)

            return coro_wrapper

        def _tagged_sync(*args: Any, **kwargs: Any):
            _tag_type(as_type)
            return fn(*args, **kwargs)

        lf_decorated = lf_observe(name=span_name, as_type=lf_as_type)(_tagged_sync)

        @wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any):
            if _test_recorder is not None:
                with _test_recorder(span_name, as_type):
                    return fn(*args, **kwargs)
            if not _enabled:
                return fn(*args, **kwargs)
            return lf_decorated(*args, **kwargs)

        return sync_wrapper

    return decorator
