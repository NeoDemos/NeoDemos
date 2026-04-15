"""Add document_date column to documents table

Revision ID: 20260415_0017
Revises: 20260415_0016_user_topic_description
Create Date: 2026-04-15

Stores the ORI start_date / last_discussed_at for civic documents so
temporal filtering ("brieven from 2024") works in search and retrieval.
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("document_date", sa.Date(), nullable=True),
    )
    op.create_index(
        "ix_documents_document_date",
        "documents",
        ["document_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_document_date", table_name="documents")
    op.drop_column("documents", "document_date")
