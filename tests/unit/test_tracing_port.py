"""TracingPort behavior tests — no real Langfuse container.

Asserts:
- disabled-mode is a transparent no-op (decorated funcs run unchanged).
- init_tracing() with placeholder Vault keys stays disabled (no SystemExit).
- init_tracing() with auth-fail raises LLMProviderError (boot check #6 fail).
- @observe wraps sync / coroutine / async-generator funcs without changing
  observed behavior.
"""

from __future__ import annotations

import pytest

from app.domain.exceptions import LLMProviderError
from app.infra import tracing


@pytest.fixture(autouse=True)
def _isolate_tracing(monkeypatch):
    tracing._reset_for_tests()
    monkeypatch.delenv("LANGFUSE_DISABLED", raising=False)
    yield
    tracing._reset_for_tests()


def test_observe_passthrough_when_disabled():
    @tracing.observe(as_type="tool", name="t.sync")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5
    assert tracing.trace_id() is None


@pytest.mark.asyncio
async def test_observe_async_passthrough_when_disabled():
    @tracing.observe(as_type="retrieval", name="t.coro")
    async def acoro(x):
        return x * 2

    assert (await acoro(7)) == 14


@pytest.mark.asyncio
async def test_observe_async_gen_passthrough_when_disabled():
    @tracing.observe(as_type="generation", name="t.gen")
    async def stream():
        yield 1
        yield 2
        yield 3

    items = [x async for x in stream()]
    assert items == [1, 2, 3]


def test_init_disabled_via_env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_DISABLED", "1")
    tracing.init_tracing()
    assert tracing._enabled is False


def test_init_disabled_with_placeholder_keys(monkeypatch):
    class _FakeVault:
        def cached(self, _p):
            return {
                "langfuse_public_key": "placeholder",
                "langfuse_secret_key": "placeholder",
                "langfuse_host": "http://langfuse:3000",
            }

    monkeypatch.setattr("app.infra.vault.get_vault", lambda: _FakeVault())
    tracing.init_tracing()  # must not raise
    assert tracing._enabled is False


def test_init_raises_on_auth_failure(monkeypatch):
    class _FakeVault:
        def cached(self, _p):
            return {
                "langfuse_public_key": "lf-pk-real",
                "langfuse_secret_key": "lf-sk-real",
                "langfuse_host": "http://langfuse:3000",
            }

    monkeypatch.setattr("app.infra.vault.get_vault", lambda: _FakeVault())

    class _FakeCtx:
        @staticmethod
        def configure(**kwargs):
            return None

        @staticmethod
        def auth_check():
            return False

    monkeypatch.setattr("langfuse.decorators.langfuse_context", _FakeCtx())

    with pytest.raises(LLMProviderError, match="auth_check"):
        tracing.init_tracing()


def test_mask_applies_redaction():
    out = tracing.mask({"key": "gsk_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"})
    assert "gsk_" not in str(out)
