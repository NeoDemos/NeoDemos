"""site_content + site_pages tables (WS8f CMS data layer)

Revision ID: 0008
Revises: 20260413_0007
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '20260413_0007'
branch_labels = None
depends_on = None


def upgrade():
    # site_content: CMS key-value store for editable text blocks
    op.execute("""
        CREATE TABLE site_content (
            id SERIAL PRIMARY KEY,
            key TEXT NOT NULL UNIQUE,
            section TEXT NOT NULL,
            label TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'text',
            value TEXT,
            default_value TEXT NOT NULL,
            help_text TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by INTEGER REFERENCES users(id)
        )
    """)
    op.execute("""
        CREATE INDEX idx_site_content_section ON site_content(section)
    """)

    # site_pages: GrapeJS page builder storage
    op.execute("""
        CREATE TABLE site_pages (
            id SERIAL PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            grapes_json TEXT,
            html_content TEXT,
            css_content TEXT,
            is_published BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by INTEGER REFERENCES users(id)
        )
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS site_pages")
    op.execute("DROP TABLE IF EXISTS site_content")
