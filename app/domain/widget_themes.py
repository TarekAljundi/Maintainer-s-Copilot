"""Widget visual themes — the six preset palettes an admin picks from.

Single source of truth. The public widget-config endpoint resolves a stored
theme *key* into the full palette (so the widget bundle stays "dumb" and just
applies whatever colours it receives), and the Streamlit admin imports `THEMES`
directly to render the picker previews.

Each palette is the complete set of CSS custom properties the widget needs:
    panel      — panel + message-area background
    surface    — header, input bar, assistant bubbles
    border     — hairline dividers
    fg         — primary text
    muted      — secondary text
    accent     — launcher, user bubbles, send button, focus ring, status dot
    on_accent  — text / icon colour on top of `accent`
"""

from __future__ import annotations

THEMES: dict[str, dict[str, str]] = {
    "midnight": {
        "label": "Midnight",
        "panel": "#0f172a",
        "surface": "#1e293b",
        "border": "#334155",
        "fg": "#f8fafc",
        "muted": "#94a3b8",
        "accent": "#22c55e",
        "on_accent": "#052e16",
    },
    "ocean": {
        "label": "Ocean",
        "panel": "#0b1a2e",
        "surface": "#15293f",
        "border": "#294b6b",
        "fg": "#f0f9ff",
        "muted": "#93b4cf",
        "accent": "#38bdf8",
        "on_accent": "#042033",
    },
    "plum": {
        "label": "Plum",
        "panel": "#1a1228",
        "surface": "#2a1f3d",
        "border": "#3f3057",
        "fg": "#f5f3ff",
        "muted": "#b8a9cf",
        "accent": "#c084fc",
        "on_accent": "#2a0e44",
    },
    "ember": {
        "label": "Ember",
        "panel": "#1c1917",
        "surface": "#2c2622",
        "border": "#44372f",
        "fg": "#faf6f0",
        "muted": "#c2b3a3",
        "accent": "#f59e0b",
        "on_accent": "#3a2606",
    },
    "rose": {
        "label": "Rose",
        "panel": "#1f1115",
        "surface": "#321a22",
        "border": "#4d2b35",
        "fg": "#fff1f2",
        "muted": "#d4a9b3",
        "accent": "#fb7185",
        "on_accent": "#4c0519",
    },
    "daylight": {
        "label": "Daylight",
        "panel": "#ffffff",
        "surface": "#f1f5f9",
        "border": "#e2e8f0",
        "fg": "#0f172a",
        "muted": "#64748b",
        "accent": "#2563eb",
        "on_accent": "#ffffff",
    },
}

DEFAULT_THEME = "midnight"
THEME_KEYS: tuple[str, ...] = tuple(THEMES.keys())


def is_valid_theme(key: str) -> bool:
    return key in THEMES


def resolve_theme(key: str | None) -> dict[str, str]:
    """Return the full palette for a theme key, falling back to the default."""
    return THEMES.get(key or "", THEMES[DEFAULT_THEME])
