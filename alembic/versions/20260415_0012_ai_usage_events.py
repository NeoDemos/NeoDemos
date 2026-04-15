"""Add ai_usage_events table — per-query cost tracking for optimisation + subscription pricing

Revision ID: 20260415_0012
Revises: 20260414_0011
Create Date: 2026-04-15
"""

from alembic import op

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage_events (
            id BIGSERIAL PRIMARY KEY,
            user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
            session_id TEXT NULL,
            ip TEXT NULL,
            endpoint TEXT NOT NULL,
            model TEXT NULL,
            query_preview TEXT NULL,
            query_length INTEGER NULL,
            tools_called TEXT[] NULL,
            rounds INTEGER NULL,
            capped BOOLEAN NOT NULL DEFAULT FALSE,
            input_tokens INTEGER NULL,
            output_tokens INTEGER NULL,
            cost_usd NUMERIC(10, 6) NULL,
            latency_ms INTEGER NULL,
            status TEXT NOT NULL,
            attached_context JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_usage_events_user_created ON ai_usage_events (user_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_usage_events_session ON ai_usage_events (session_id, created_at DESC)")
    # Avoid date_trunc() in an index expression (not IMMUTABLE on TIMESTAMPTZ).
    # Simple composite on (endpoint, created_at DESC) lets queries do the
    # per-day rollup at query time via date_trunc or generate_series.
    op.execute("CREATE INDEX IF NOT EXISTS ix_ai_usage_events_endpoint_created ON ai_usage_events (endpoint, created_at DESC)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_ai_usage_events_endpoint_created")
    op.execute("DROP INDEX IF EXISTS ix_ai_usage_events_session")
    op.execute("DROP INDEX IF EXISTS ix_ai_usage_events_user_created")
    op.execute("DROP TABLE IF EXISTS ai_usage_events")
