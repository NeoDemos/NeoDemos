"""user avatar_slug + subscription_tier_override + pro_expires_at (WS8f Profiel upgrade)

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-15

Additive-only columns on users. All NULL by default.

- avatar_slug: the Gemini-portrait slug the user picked on /settings
  (NULL = fall back to deterministic hash of email/id)
- subscription_tier_override: the user's own pick from /settings
  ('gratis' | 'pro'). NULL = use legacy subscription_tier column.
- pro_expires_at: when a beta-pro user's free period ends. NULL = no
  expiry set yet. Will be stamped when a user picks Pro during beta.
"""
from alembic import op


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    # Short lock_timeout so a busy users table (OAuth lookups, active sessions)
    # doesn't stall production auth. Retry if it times out; the ALTERs are
    # metadata-only (nullable, no default) so they're fast on Postgres 11+.
    op.execute("SET lock_timeout = '3s'")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_slug TEXT NULL")
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_tier_override TEXT NULL"
    )
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS pro_expires_at TIMESTAMPTZ NULL"
    )
    op.execute("SET lock_timeout = '0'")


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS pro_expires_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS subscription_tier_override")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_slug")
