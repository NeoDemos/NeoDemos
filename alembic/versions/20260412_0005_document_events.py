"""Add document_events table for per-document activity logging.

Tracks every significant event for a document: downloaded, chunked,
financial_detected, financial_extracted, promoted, etc. This gives
the operator a queryable timeline of what the system did and when.

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
        CREATE TABLE IF NOT EXISTS document_events (
            id BIGSERIAL PRIMARY KEY,
            document_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            details JSONB,
            triggered_by TEXT NOT NULL DEFAULT 'system'
        );

        CREATE INDEX IF NOT EXISTS idx_docevents_docid
            ON document_events (document_id);
        CREATE INDEX IF NOT EXISTS idx_docevents_type_at
            ON document_events (event_type, event_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_events;")
