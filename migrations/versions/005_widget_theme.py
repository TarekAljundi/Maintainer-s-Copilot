"""widget_configs.theme — six-preset visual theme picker

Revision ID: 005_widget_theme
Revises: 004_widgets
Create Date: 2026-05-22

Adds a `theme` column to widget_configs. The admin picks one of six preset
palettes (see app/domain/widget_themes.py); the public widget-config endpoint
resolves the stored key into a full palette for the widget bundle.

Existing rows default to 'midnight', which matches the previous hard-coded dark
look — non-destructive. The theme keys are spelled out in the CHECK constraint
verbatim (a migration is a point-in-time snapshot — it must not import app code
that can drift).
"""

from __future__ import annotations

from alembic import op


revision = "005_widget_theme"
down_revision = "004_widgets"
branch_labels = None
depends_on = None

_THEME_KEYS = ("midnight", "ocean", "plum", "ember", "rose", "daylight")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE widget_configs "
        "ADD COLUMN theme VARCHAR(16) NOT NULL DEFAULT 'midnight'"
    )
    keys = ", ".join(f"'{k}'" for k in _THEME_KEYS)
    op.execute(
        "ALTER TABLE widget_configs "
        f"ADD CONSTRAINT widget_configs_theme_ck CHECK (theme IN ({keys}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE widget_configs DROP CONSTRAINT IF EXISTS widget_configs_theme_ck")
    op.execute("ALTER TABLE widget_configs DROP COLUMN IF EXISTS theme")
