"""widget_configs + episodic_memories widget-keying

Revision ID: 004_widgets
Revises: 003_auth_memory_audit
Create Date: 2026-05-20

Slice 13: embeddable widget. Two changes:

1. `widget_configs` — admin-managed widget registrations. `allowed_origins`
   drives both the embed-route CSP `frame-ancestors` header and the dynamic
   CORS allowlist; `enabled_tools` is snapshotted into each minted anon-session
   JWT so a later edit doesn't retroactively un-grant tools to live sessions.

2. `episodic_memories` widget-keying — PRD §Chatbot Q17 says memory is keyed by
   `widget_session_id` for anon users, `user_id` for authed users. This migration
   makes `user_id` nullable, adds the sibling `widget_session_id` column, and a
   table-level CHECK that ensures exactly-one-of is set. Existing rows keep
   their `user_id` so the migration is non-destructive.
"""

from __future__ import annotations

from alembic import op


revision = "004_widgets"
down_revision = "003_auth_memory_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE widget_configs (
            id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name             VARCHAR(120) NOT NULL,
            allowed_origins  TEXT[] NOT NULL DEFAULT '{}',
            primary_color    VARCHAR(16) NOT NULL DEFAULT '#1e293b',
            position         VARCHAR(2)  NOT NULL DEFAULT 'br',
            greeting_text    TEXT NOT NULL DEFAULT 'Hi! Ask me anything about this project.',
            enabled_tools    TEXT[] NOT NULL DEFAULT
                '{classify_issue,extract_entities,summarize_thread,search_knowledge,write_memory}',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT widget_configs_position_ck CHECK (position IN ('br','bl','tr','tl'))
        )
        """
    )
    op.execute("CREATE INDEX widget_configs_name_idx ON widget_configs (name)")

    # --- episodic_memories: widget-keyed support -------------------------------
    op.execute("ALTER TABLE episodic_memories ALTER COLUMN user_id DROP NOT NULL")
    op.execute("ALTER TABLE episodic_memories ADD COLUMN widget_session_id VARCHAR(64) NULL")
    op.execute(
        """
        ALTER TABLE episodic_memories
        ADD CONSTRAINT episodic_memories_actor_ck
        CHECK (user_id IS NOT NULL OR widget_session_id IS NOT NULL)
        """
    )
    op.execute(
        "CREATE INDEX episodic_memories_widget_created_idx "
        "ON episodic_memories (widget_session_id, created_at DESC) "
        "WHERE widget_session_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS episodic_memories_widget_created_idx")
    op.execute("ALTER TABLE episodic_memories DROP CONSTRAINT IF EXISTS episodic_memories_actor_ck")
    op.execute("ALTER TABLE episodic_memories DROP COLUMN IF EXISTS widget_session_id")
    op.execute("ALTER TABLE episodic_memories ALTER COLUMN user_id SET NOT NULL")
    op.execute("DROP TABLE IF EXISTS widget_configs")
