"""Add municipality and source columns to documents table.

municipality: which city this document belongs to (default 'rotterdam').
  Enables multi-city filtering in retrieval without re-embedding.

source: where this document was ingested from ('ori', 'ibabs', 'scraper', 'manual').
  Critical for dedup logic, gap analysis, and future multi-source reconciliation.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0006'
down_revision: Union[str, None] = '0005b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE documents ADD COLUMN IF NOT EXISTS municipality VARCHAR(50) NOT NULL DEFAULT 'rotterdam';
    """)
    op.execute("""
        COMMENT ON COLUMN documents.municipality IS
            'Municipality this document belongs to (e.g. rotterdam, amsterdam). DEFAULT rotterdam. Enables multi-city retrieval filtering.';
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_municipality
            ON documents(municipality);
    """)

    op.execute("""
        ALTER TABLE documents ADD COLUMN IF NOT EXISTS source VARCHAR(50);
    """)
    op.execute("""
        COMMENT ON COLUMN documents.source IS
            'Ingestion source: ori, ibabs, scraper, manual. NULL for pre-WS11 legacy documents.';
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_documents_source
            ON documents(source)
            WHERE source IS NOT NULL;
    """)

    # Update doc_classification column comment to reflect its evolved primary role
    op.execute("""
        COMMENT ON COLUMN documents.doc_classification IS
            'Civic document type (primary): schriftelijke_vraag, initiatiefnotitie, initiatiefvoorstel,
             motie, amendement, raadsvoorstel, toezegging, transcript.
             Processing-type fallback values (only set when civic type is absent):
             financial, financial_table_rich, garbled_ocr, garbled_table_rich, table_rich, regular.
             NULL = not yet classified.';
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_documents_source;")
    op.execute("DROP INDEX IF EXISTS idx_documents_municipality;")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS source;")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS municipality;")
