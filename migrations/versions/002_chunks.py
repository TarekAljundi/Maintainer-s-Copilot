"""chunks table + pgvector HNSW + FTS

Revision ID: 002_chunks
Revises: 001_init
Create Date: 2026-05-20

Unified `chunks` table discriminated by `content_type` ('docs' | 'issue').
Schema supports parent-doc retrieval (parent_id), hybrid retrieval (tsv),
and metadata filtering (labels GIN, is_answer, closed_at, breadcrumb).
Slice 06 only writes dense queries; FTS / parent / filter wiring lands in 07.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "002_chunks"
down_revision = "001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE chunks (
            id              VARCHAR(16) PRIMARY KEY,
            content_type    VARCHAR(16) NOT NULL,
            parent_id       VARCHAR(16) NULL REFERENCES chunks(id) ON DELETE SET NULL,
            source_id       VARCHAR(512) NOT NULL,
            chunk_seq       INTEGER NOT NULL,
            text            TEXT NOT NULL,
            embedding       VECTOR(768) NOT NULL,
            tsv             TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
            breadcrumb      TEXT NULL,
            section_path    VARCHAR(512) NULL,
            labels          TEXT[] NULL,
            is_answer       BOOLEAN NULL,
            closed_at       TIMESTAMPTZ NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chunks_content_type_ck
                CHECK (content_type IN ('docs', 'issue'))
        )
        """
    )

    op.execute(
        "CREATE INDEX chunks_embedding_hnsw_idx ON chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX chunks_tsv_gin_idx ON chunks USING GIN (tsv)")
    op.execute("CREATE INDEX chunks_labels_gin_idx ON chunks USING GIN (labels)")
    op.execute("CREATE INDEX chunks_source_idx ON chunks (content_type, source_id)")
    op.execute("CREATE INDEX chunks_parent_idx ON chunks (parent_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunks")
