"""Add embedded_at timestamp to document_chunks (replaces legacy vector(3072) column)

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-14

The legacy `document_chunks.embedding` column was `vector(3072)` from an old
Qwen 2.5 embedding model. The current embedder (Nebius Qwen3-Embedding-8B) is
4096D, and Qdrant is the actual source of truth for embeddings. The Postgres
column was effectively dead weight and the Phase 2 write code in
services/document_processor.py was failing silently every 20 minutes, causing
the scheduler to waste Nebius API calls re-embedding the corpus repeatedly.

This migration replaces the column with a lightweight `embedded_at` timestamp
used solely as a "this chunk is in Qdrant" marker.

The legacy `embedding vector(3072)` column is NOT dropped here — drop it in a
follow-up migration once the new code path is stable (~20GB storage reclaim
at 1.74M rows).
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ"
    )
    # Partial index speeds up "find unembedded chunks" in Phase 2
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_chunks_unembedded "
        "ON document_chunks (id) WHERE embedded_at IS NULL"
    )
    # Backfill: all existing chunks are assumed to be in Qdrant (the production
    # pipeline has been running for weeks; Qdrant has 1.77M+ points covering
    # the 1.74M Postgres chunks). Mark them as embedded so Phase 2 doesn't
    # wastefully re-embed them.
    op.execute(
        "UPDATE document_chunks SET embedded_at = NOW() WHERE embedded_at IS NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_unembedded")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedded_at")
