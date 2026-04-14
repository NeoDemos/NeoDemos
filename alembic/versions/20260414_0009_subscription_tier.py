"""subscription_tier + beta columns on users (WS8f)

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    # Use a short lock_timeout so if the users table is busy (e.g. active ingestion or
    # OAuth lookups holding row locks), this migration fails fast instead of blocking
    # production authentication. Retry during a quiet window if it times out.
    #
    # Columns are nullable with a DEFAULT. Postgres 11+ stores DEFAULT as metadata
    # (fast), so no table rewrite. NOT NULL can be added later in a separate
    # maintenance-window migration once all existing rows are backfilled by the default.
    op.execute("SET lock_timeout = '3s'")
    op.execute("""
        ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free_beta'
    """)
    op.execute("""
        ALTER TABLE users ADD COLUMN beta_expires_at TIMESTAMP
    """)
    op.execute("""
        ALTER TABLE users ADD COLUMN stripe_customer_id TEXT
    """)
    op.execute("SET lock_timeout = '0'")


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS stripe_customer_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS beta_expires_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS subscription_tier")
