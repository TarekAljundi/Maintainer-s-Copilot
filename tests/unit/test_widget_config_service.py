"""WidgetConfigService cache behavior — no Postgres needed.

Stubs:
- A FakeRedis (`get/set/delete`) that records calls.
- A fake `app.repositories.widget_configs` (get/create/update/delete) backed
  by an in-memory dict, monkey-patched at the module level.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domain.widget import WidgetConfig
from app.services import widget_config as svc_mod


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    async def get(self, k: str):
        self.calls.append(("get", k))
        return self.store.get(k)

    async def set(self, k: str, v: str, ex: int | None = None):
        self.calls.append(("set", k))
        self.store[k] = v

    async def delete(self, *ks: str):
        for k in ks:
            self.calls.append(("delete", k))
            self.store.pop(k, None)


def _make_cfg(cid: str = "11111111-1111-1111-1111-111111111111") -> WidgetConfig:
    now = datetime.now(timezone.utc)
    return WidgetConfig(
        id=cid,
        name="demo",
        allowed_origins=("http://localhost:8087",),
        primary_color="#222",
        position="br",
        greeting_text="hi",
        enabled_tools=("classify_issue",),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def patched(monkeypatch):
    rows: dict[str, WidgetConfig] = {}
    cfg = _make_cfg()
    rows[cfg.id] = cfg

    async def fake_get(wid: str):
        return rows.get(wid)

    async def fake_create(**kwargs: Any):
        new_id = "22222222-2222-2222-2222-222222222222"
        rows[new_id] = WidgetConfig(
            id=new_id,
            name=kwargs["name"],
            allowed_origins=tuple(kwargs["allowed_origins"]),
            primary_color=kwargs.get("primary_color", "#222"),
            position=kwargs.get("position", "br"),
            greeting_text=kwargs.get("greeting_text", ""),
            enabled_tools=tuple(kwargs.get("enabled_tools") or ()),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return new_id

    async def fake_update(wid: str, **patch: Any):
        if wid not in rows:
            return None
        old = rows[wid]
        rows[wid] = WidgetConfig(
            id=old.id,
            name=patch.get("name", old.name),
            allowed_origins=tuple(patch.get("allowed_origins", old.allowed_origins)),
            primary_color=patch.get("primary_color", old.primary_color),
            position=patch.get("position", old.position),
            greeting_text=patch.get("greeting_text", old.greeting_text),
            enabled_tools=tuple(patch.get("enabled_tools", old.enabled_tools)),
            created_at=old.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        return rows[wid]

    async def fake_delete(wid: str):
        return rows.pop(wid, None) is not None

    async def fake_list_(limit: int = 100, offset: int = 0):
        return list(rows.values())[offset : offset + limit]

    monkeypatch.setattr(svc_mod.repo, "get", fake_get)
    monkeypatch.setattr(svc_mod.repo, "create", fake_create)
    monkeypatch.setattr(svc_mod.repo, "update", fake_update)
    monkeypatch.setattr(svc_mod.repo, "delete", fake_delete)
    monkeypatch.setattr(svc_mod.repo, "list_", fake_list_)
    return cfg


async def test_get_caches_after_first_read(patched):
    cfg = patched
    r = FakeRedis()
    s = svc_mod.WidgetConfigService(redis_client=r)

    first = await s.get(cfg.id)
    assert first.name == cfg.name
    cache_keys_after_first = [c for c in r.calls if c[0] == "set"]
    assert any(k == f"widget_config:{cfg.id}" for _, k in cache_keys_after_first)

    # Second call should be a cache hit — no repo.get re-read needed.
    second = await s.get(cfg.id)
    assert second.id == cfg.id


async def test_update_invalidates_cache_with_fresh_write(patched):
    cfg = patched
    r = FakeRedis()
    s = svc_mod.WidgetConfigService(redis_client=r)
    await s.get(cfg.id)  # warm the cache
    updated = await s.update(cfg.id, name="renamed")
    assert updated.name == "renamed"
    # Cache should now have the fresh value (service writes through on update).
    raw = r.store[f"widget_config:{cfg.id}"]
    assert "renamed" in raw


async def test_delete_evicts_cache(patched):
    cfg = patched
    r = FakeRedis()
    s = svc_mod.WidgetConfigService(redis_client=r)
    await s.get(cfg.id)
    assert f"widget_config:{cfg.id}" in r.store
    ok = await s.delete(cfg.id)
    assert ok is True
    assert f"widget_config:{cfg.id}" not in r.store


async def test_allowed_origins_uses_cache(patched):
    cfg = patched
    r = FakeRedis()
    s = svc_mod.WidgetConfigService(redis_client=r)
    origins = await s.allowed_origins(cfg.id)
    assert origins == ("http://localhost:8087",)


async def test_get_unknown_raises_notfound(patched):
    r = FakeRedis()
    s = svc_mod.WidgetConfigService(redis_client=r)
    from app.domain.exceptions import NotFoundError

    with pytest.raises(NotFoundError):
        await s.get("ffffffff-ffff-ffff-ffff-ffffffffffff")
