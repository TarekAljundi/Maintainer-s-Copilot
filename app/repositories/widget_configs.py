"""Raw-SQL repo for widget_configs. Returns the WidgetConfig dataclass."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domain.widget import ALLOWED_POSITIONS, DEFAULT_ENABLED_TOOLS, WidgetConfig
from app.infra.db import acquire


def _row_to_config(row: Any) -> WidgetConfig:
    return WidgetConfig(
        id=str(row["id"]),
        name=row["name"],
        allowed_origins=tuple(row["allowed_origins"] or ()),
        primary_color=row["primary_color"],
        position=row["position"],
        greeting_text=row["greeting_text"],
        enabled_tools=tuple(row["enabled_tools"] or ()),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        theme=row["theme"],
    )


async def get(widget_id: str) -> WidgetConfig | None:
    try:
        uid = UUID(widget_id)
    except (ValueError, TypeError):
        return None
    async with acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM widget_configs WHERE id = $1", uid)
    return _row_to_config(row) if row else None


async def list_(limit: int = 100, offset: int = 0) -> list[WidgetConfig]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM widget_configs ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
    return [_row_to_config(r) for r in rows]


async def create(
    *,
    name: str,
    allowed_origins: list[str],
    primary_color: str = "#1e293b",
    position: str = "br",
    greeting_text: str = "Hi! Ask me anything about this project.",
    enabled_tools: list[str] | None = None,
    theme: str = "midnight",
) -> str:
    if position not in ALLOWED_POSITIONS:
        raise ValueError(f"position must be one of {ALLOWED_POSITIONS}")
    tools = enabled_tools if enabled_tools is not None else list(DEFAULT_ENABLED_TOOLS)
    async with acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO widget_configs
                (name, allowed_origins, primary_color, position, greeting_text,
                 enabled_tools, theme)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            name,
            allowed_origins,
            primary_color,
            position,
            greeting_text,
            tools,
            theme,
        )
    return str(row["id"])


_PATCHABLE = {
    "name",
    "allowed_origins",
    "primary_color",
    "position",
    "greeting_text",
    "enabled_tools",
    "theme",
}


async def update(widget_id: str, **patch: Any) -> WidgetConfig | None:
    """Patch a row in place. Unknown keys raise ValueError; empty patch is a
    no-op read."""
    unknown = set(patch) - _PATCHABLE
    if unknown:
        raise ValueError(f"unknown widget_configs field(s): {sorted(unknown)}")
    if "position" in patch and patch["position"] not in ALLOWED_POSITIONS:
        raise ValueError(f"position must be one of {ALLOWED_POSITIONS}")
    if not patch:
        return await get(widget_id)
    try:
        uid = UUID(widget_id)
    except (ValueError, TypeError):
        return None
    sets: list[str] = []
    args: list[Any] = []
    for i, (k, v) in enumerate(patch.items(), start=1):
        sets.append(f"{k} = ${i}")
        args.append(v)
    sets.append("updated_at = now()")
    args.append(uid)
    sql = f"UPDATE widget_configs SET {', '.join(sets)} WHERE id = ${len(args)} RETURNING *"
    async with acquire() as conn:
        row = await conn.fetchrow(sql, *args)
    return _row_to_config(row) if row else None


async def delete(widget_id: str) -> bool:
    try:
        uid = UUID(widget_id)
    except (ValueError, TypeError):
        return False
    async with acquire() as conn:
        result = await conn.execute("DELETE FROM widget_configs WHERE id = $1", uid)
    return result.endswith(" 1")
