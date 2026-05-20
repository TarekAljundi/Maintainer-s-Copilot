"""WidgetConfigService — Redis-cached read-through + invalidate-on-write.

PRD §Widget and embed flow: `allowed_origins` (security boundary) and the
visual surface are admin-managed; widget config is a hot read for every
embed/session/CORS check, so we keep a 60s Redis TTL with explicit
invalidation on writes.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.exceptions import NotFoundError
from app.domain.widget import WidgetConfig
from app.repositories import widget_configs as repo


CACHE_TTL_SECONDS = 60
_KEY_FMT = "widget_config:{id}"
_LIST_KEY = "widget_config:list:v1"


def _serialize(c: WidgetConfig) -> str:
    return json.dumps(
        {
            "id": c.id,
            "name": c.name,
            "allowed_origins": list(c.allowed_origins),
            "primary_color": c.primary_color,
            "position": c.position,
            "greeting_text": c.greeting_text,
            "enabled_tools": list(c.enabled_tools),
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
    )


def _deserialize(raw: str) -> WidgetConfig:
    from datetime import datetime

    d = json.loads(raw)
    return WidgetConfig(
        id=d["id"],
        name=d["name"],
        allowed_origins=tuple(d["allowed_origins"]),
        primary_color=d["primary_color"],
        position=d["position"],
        greeting_text=d["greeting_text"],
        enabled_tools=tuple(d["enabled_tools"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
    )


class WidgetConfigService:
    """Hot reads via Redis (60s TTL); writes invalidate the per-id key + the
    list key. Cache misses fall through to the repo."""

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client

    async def _r(self):
        if self._redis is not None:
            return self._redis
        from app.infra.redis import get_redis

        return await get_redis()

    async def get(self, widget_id: str) -> WidgetConfig:
        key = _KEY_FMT.format(id=widget_id)
        try:
            r = await self._r()
            cached = await r.get(key)
            if cached:
                return _deserialize(cached)
        except Exception:
            # Cache outage falls through to the DB; we never block on Redis.
            pass

        cfg = await repo.get(widget_id)
        if cfg is None:
            raise NotFoundError(f"widget config {widget_id!r} not found", widget_id=widget_id)

        try:
            r = await self._r()
            await r.set(key, _serialize(cfg), ex=CACHE_TTL_SECONDS)
        except Exception:
            pass
        return cfg

    async def get_or_none(self, widget_id: str) -> WidgetConfig | None:
        try:
            return await self.get(widget_id)
        except NotFoundError:
            return None

    async def list_(self, limit: int = 100, offset: int = 0) -> list[WidgetConfig]:
        # List is admin-only; skip caching it to avoid stale CRUD reads.
        return await repo.list_(limit=limit, offset=offset)

    async def create(self, **kwargs) -> WidgetConfig:
        new_id = await repo.create(**kwargs)
        # Warm the per-id cache off the fresh row so subsequent reads stay hot.
        fresh = await repo.get(new_id)
        if fresh is None:
            raise NotFoundError("widget vanished post-create", widget_id=new_id)
        try:
            r = await self._r()
            await r.set(_KEY_FMT.format(id=new_id), _serialize(fresh), ex=CACHE_TTL_SECONDS)
        except Exception:
            pass
        return fresh

    async def update(self, widget_id: str, **patch) -> WidgetConfig:
        updated = await repo.update(widget_id, **patch)
        if updated is None:
            raise NotFoundError(f"widget config {widget_id!r} not found", widget_id=widget_id)
        try:
            r = await self._r()
            await r.set(_KEY_FMT.format(id=widget_id), _serialize(updated), ex=CACHE_TTL_SECONDS)
        except Exception:
            pass
        return updated

    async def delete(self, widget_id: str) -> bool:
        ok = await repo.delete(widget_id)
        if ok:
            try:
                r = await self._r()
                await r.delete(_KEY_FMT.format(id=widget_id))
            except Exception:
                pass
        return ok

    async def allowed_origins(self, widget_id: str) -> tuple[str, ...]:
        cfg = await self.get_or_none(widget_id)
        return cfg.allowed_origins if cfg else ()


_default: WidgetConfigService | None = None


def default_service() -> WidgetConfigService:
    global _default
    if _default is None:
        _default = WidgetConfigService()
    return _default
