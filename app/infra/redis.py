"""Redis client. Keys + TTLs per PRD Q18 (conv 24h, tool 1h, embed 24h, RL 60s)."""

from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis, from_url

from app.infra.vault import get_vault


_client: Optional[Redis] = None


def _url() -> str:
    secrets = get_vault().cached("api/redis")
    return secrets.get("url") or "redis://redis:6379/0"


async def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = from_url(_url(), decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
