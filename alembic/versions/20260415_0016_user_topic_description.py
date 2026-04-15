"""user topic_description (WS-pricing tier model)

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-15

Additive-only column on users. NULL by default.

- topic_description: free-text field (max 500 chars enforced at app layer)
  where the user describes their policy focus / aandachtsgebieden. Injected
  into the web + MCP system prompt so answers weigh these topics without
  overriding the explicit question.
"""
from alembic import op


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    # Short lock_timeout per memory feedback_mcp_uptime — users table is hot
    # (OAuth + session lookups on every request). Metadata-only ALTER on
    # Postgres 11+ so it's fast when the lock is granted.
    op.execute("SET lock_timeout = '3s'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS topic_description TEXT NULL")
    op.execute("SET lock_timeout = '0'")


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS topic_description")
