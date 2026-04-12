"""Add ocr_quality column to documents table (WS7 OCR Recovery).

Tracks per-document OCR quality so WS6 summarization and other consumers
can skip or deprioritise documents that need re-OCR.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE documents ADD COLUMN IF NOT EXISTS ocr_quality VARCHAR(20);
    """)
    op.execute("""
        COMMENT ON COLUMN documents.ocr_quality IS
            'OCR quality flag: NULL=not assessed, good=clean text, degraded=minor issues, bad=needs re-OCR (set by WS7 recovery)';
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_ocr_quality
            ON documents(ocr_quality)
            WHERE ocr_quality IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_ocr_quality;")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS ocr_quality;")
