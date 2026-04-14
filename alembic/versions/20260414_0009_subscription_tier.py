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
    op.execute("""
        ALTER TABLE users ADD COLUMN subscription_tier TEXT NOT NULL DEFAULT 'free_beta'
    """)
    op.execute("""
        ALTER TABLE users ADD COLUMN beta_expires_at TIMESTAMP
    """)
    op.execute("""
        ALTER TABLE users ADD COLUMN stripe_customer_id TEXT
    """)


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS stripe_customer_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS beta_expires_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS subscription_tier")
