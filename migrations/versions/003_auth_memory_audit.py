"""users + episodic_memories + audit_log

Revision ID: 003_auth_memory_audit
Revises: 002_chunks
Create Date: 2026-05-20

Slices 08 (redaction's memory boundary), 10 (auth), 11 (memory) land together.

users: fastapi-users-compatible. UUID PK, email unique, hashed_password,
       is_active/superuser/verified, role CHECK in {user,admin}.

episodic_memories: PRD §Chatbot Q17. user_id scoped, parent over conversations
       (FK is on user only — conversation_id is a string, no FK because the
       conversations table lands with the broader chat-persistence work
       outside this slice). HNSW on embedding, GIN on entities, btree on
       (user_id, created_at DESC).

audit_log: single table for memory write/recall + role changes + widget
       config changes + conversation deletions (per PRD user story 22).
       JSONB payload absorbs per-action detail without per-domain tables.
"""

from __future__ import annotations

from alembic import op


revision = "003_auth_memory_audit"
down_revision = "002_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.execute(
        """
        CREATE TABLE users (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            email           VARCHAR(320) NOT NULL UNIQUE,
            hashed_password VARCHAR(1024) NOT NULL,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            is_superuser    BOOLEAN NOT NULL DEFAULT FALSE,
            is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
            role            VARCHAR(16) NOT NULL DEFAULT 'user',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT users_role_ck CHECK (role IN ('user', 'admin'))
        )
        """
    )
    op.execute("CREATE INDEX users_email_idx ON users (email)")

    op.execute(
        """
        CREATE TABLE episodic_memories (
            id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            conversation_id  VARCHAR(64) NULL,
            memory_type      VARCHAR(16) NOT NULL DEFAULT 'episodic',
            summary          TEXT NOT NULL,
            entities         TEXT[] NULL,
            source_msg_ids   TEXT[] NULL,
            embedding        VECTOR(768) NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_recalled_at TIMESTAMPTZ NULL,
            CONSTRAINT episodic_memories_type_ck CHECK (memory_type IN ('episodic'))
        )
        """
    )
    op.execute(
        "CREATE INDEX episodic_memories_embedding_hnsw_idx ON episodic_memories "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE INDEX episodic_memories_entities_gin_idx ON episodic_memories "
        "USING GIN (entities)"
    )
    op.execute(
        "CREATE INDEX episodic_memories_user_created_idx ON episodic_memories "
        "(user_id, created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE audit_log (
            id           BIGSERIAL PRIMARY KEY,
            actor        VARCHAR(64) NOT NULL,
            action       VARCHAR(64) NOT NULL,
            target_type  VARCHAR(32) NOT NULL,
            target_id    VARCHAR(64) NULL,
            ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
            trace_id     VARCHAR(64) NULL,
            payload      JSONB NULL
        )
        """
    )
    op.execute("CREATE INDEX audit_log_actor_ts_idx ON audit_log (actor, ts DESC)")
    op.execute("CREATE INDEX audit_log_action_idx ON audit_log (action)")
    op.execute("CREATE INDEX audit_log_target_idx ON audit_log (target_type, target_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS episodic_memories")
    op.execute("DROP TABLE IF EXISTS users")
