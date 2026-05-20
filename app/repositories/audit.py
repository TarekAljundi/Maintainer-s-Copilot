"""Audit-log writes — single shared table.

PRD §user story 22: admin views audit log of memory writes/recalls + role
changes + widget config changes + conversation deletions. One table, JSONB
payload, action column discriminates.
"""

from __future__ import annotations

import json
from typing import Any

from app.infra.db import acquire
from app.infra.tracing import trace_id


async def write_audit(
    *,
    actor: str,
    action: str,
    target_type: str,
    target_id: str | None,
    payload: dict[str, Any] | None = None,
    conn=None,
) -> int:
    """Insert one audit_log row. Returns the row id.

    If `conn` is provided, runs on that connection (caller controls the
    transaction — used by MemoryService.write to keep memory + audit atomic).
    Otherwise acquires its own connection.
    """
    payload_json = json.dumps(payload) if payload is not None else None
    tid = trace_id()
    sql = (
        "INSERT INTO audit_log (actor, action, target_type, target_id, trace_id, payload) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING id"
    )
    if conn is not None:
        return await conn.fetchval(sql, actor, action, target_type, target_id, tid, payload_json)
    async with acquire() as c:
        return await c.fetchval(sql, actor, action, target_type, target_id, tid, payload_json)


async def list_for_actor(actor: str, limit: int = 100) -> list[dict[str, Any]]:
    """Read-only view of an actor's audit history. Admin endpoint surface."""
    async with acquire() as c:
        rows = await c.fetch(
            "SELECT id, actor, action, target_type, target_id, ts, trace_id, payload "
            "FROM audit_log WHERE actor = $1 ORDER BY ts DESC LIMIT $2",
            actor,
            limit,
        )
    return [dict(r) for r in rows]
