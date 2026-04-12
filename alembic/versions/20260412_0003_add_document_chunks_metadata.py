"""Add metadata TEXT column to document_chunks (public + staging).

Stores per-chunk OCR quality metadata as a JSON string:
  {"ocr_confidence": {"ocr_score": ..., "table_score": ..., ...},
   "is_ocr_fallback": bool, "page_number": int}

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Public schema
    op.execute("""
        ALTER TABLE document_chunks
        ADD COLUMN IF NOT EXISTS metadata TEXT;
    """)
    # Staging schema
    op.execute("""
        ALTER TABLE staging.document_chunks
        ADD COLUMN IF NOT EXISTS metadata TEXT;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE staging.document_chunks
        DROP COLUMN IF EXISTS metadata;
    """)
    op.execute("""
        ALTER TABLE document_chunks
        DROP COLUMN IF EXISTS metadata;
    """)
