"""Add document_relationships table for motie↔afdoeningsvoorstel and motie↔raadsvoorstel links

Revision ID: 20260414_0011
Revises: 20260414_0010
Create Date: 2026-04-14
"""

from alembic import op

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS document_relationships (
            id SERIAL PRIMARY KEY,
            source_doc_id TEXT NOT NULL,
            target_doc_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            confidence FLOAT NOT NULL DEFAULT 1.0,
            method TEXT,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_rel_source_target_type
        ON document_relationships (source_doc_id, target_doc_id, relation_type)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_doc_rel_source
        ON document_relationships (source_doc_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_doc_rel_target
        ON document_relationships (target_doc_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_doc_rel_relation_type
        ON document_relationships (relation_type)
    """)


def downgrade():
    op.drop_table('document_relationships')
