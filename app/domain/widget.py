"""Widget domain types — admin-managed widget config + the public-read view.

Two views of the same row:

- `WidgetConfig`: full admin view, includes `allowed_origins` (security-sensitive
  — never leaked through `/widget/{id}/config`).
- `WidgetPublicConfig`: what the loader script + bundle see. No origin list, no
  audit fields — just the visual + functional surface the embedded chat needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

ALLOWED_POSITIONS: tuple[str, ...] = ("br", "bl", "tr", "tl")

DEFAULT_ENABLED_TOOLS: tuple[str, ...] = (
    "classify_issue",
    "extract_entities",
    "summarize_thread",
    "search_knowledge",
    "write_memory",
)


@dataclass(frozen=True, slots=True)
class WidgetConfig:
    id: str
    name: str
    allowed_origins: tuple[str, ...]
    primary_color: str
    position: str
    greeting_text: str
    enabled_tools: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    # `theme` is a preset key (see app/domain/widget_themes.py). Last field
    # with a default so existing keyword constructors stay valid.
    theme: str = "midnight"


@dataclass(frozen=True, slots=True)
class WidgetPublicConfig:
    """Subset of WidgetConfig returned by GET /widget/{id}/config."""

    id: str
    name: str
    primary_color: str
    position: str
    greeting_text: str
    enabled_tools: tuple[str, ...]
    theme: str = "midnight"

    @classmethod
    def from_config(cls, c: WidgetConfig) -> "WidgetPublicConfig":
        return cls(
            id=c.id,
            name=c.name,
            primary_color=c.primary_color,
            position=c.position,
            greeting_text=c.greeting_text,
            enabled_tools=c.enabled_tools,
            theme=c.theme,
        )
