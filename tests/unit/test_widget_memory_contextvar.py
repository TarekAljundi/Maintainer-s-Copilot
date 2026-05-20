"""write_memory routing per ContextVar — exactly-one-of {user, widget_session}.

The tool reads `current_user_id` xor `current_widget_session_id` and forwards
to `MemoryService.write`. We stub the service to capture the kwargs.
"""

from __future__ import annotations

import pytest

from app.domain import tools as tools_mod
from app.domain.exceptions import MemoryWriteFailure


class _FakeMemorySvc:
    def __init__(self) -> None:
        self.called_with: dict | None = None

    async def write(self, **kwargs):
        self.called_with = kwargs
        return "memory-id-1"


@pytest.fixture
def fake_svc(monkeypatch) -> _FakeMemorySvc:
    fake = _FakeMemorySvc()
    monkeypatch.setattr("app.services.memory.default_service", lambda: fake)
    return fake


async def test_write_memory_routes_to_user(fake_svc):
    tok = tools_mod.current_user_id.set("11111111-1111-1111-1111-111111111111")
    try:
        result = await tools_mod._tool_write_memory(summary="ok")
    finally:
        tools_mod.current_user_id.reset(tok)
    assert result == {"ok": True, "memory_id": "memory-id-1"}
    assert fake_svc.called_with["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert fake_svc.called_with["widget_session_id"] is None


async def test_write_memory_routes_to_widget(fake_svc):
    tok = tools_mod.current_widget_session_id.set("widget-uuid-abc")
    try:
        result = await tools_mod._tool_write_memory(summary="ok")
    finally:
        tools_mod.current_widget_session_id.reset(tok)
    assert result == {"ok": True, "memory_id": "memory-id-1"}
    assert fake_svc.called_with["widget_session_id"] == "widget-uuid-abc"
    assert fake_svc.called_with["user_id"] is None


async def test_write_memory_raises_when_no_principal(fake_svc):
    with pytest.raises(MemoryWriteFailure, match="requires_authed_user"):
        await tools_mod._tool_write_memory(summary="ok")
