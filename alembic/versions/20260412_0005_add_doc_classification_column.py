"""Add doc_classification column to documents table.

Classifies each document into one of several categories so downstream
consumers (summariser, RAG retriever, quality dashboards) can apply
type-specific logic.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_classification VARCHAR(30);
    """)
    op.execute("""
        COMMENT ON COLUMN documents.doc_classification IS
            'Document type classification: transcript, financial, financial_table_rich, garbled_ocr, garbled_table_rich, table_rich, regular';
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_doc_classification
            ON documents(doc_classification)
            WHERE doc_classification IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_doc_classification;")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS doc_classification;")
