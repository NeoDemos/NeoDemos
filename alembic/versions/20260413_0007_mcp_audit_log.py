"""mcp_audit_log table + api_tokens.token_hash column (WS4)

Revision ID: 20260413_0007
Revises: 0006
Create Date: 2026-04-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '20260413_0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    # mcp_audit_log
    op.create_table(
        'mcp_audit_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.TIMESTAMP(timezone=True), server_default=sa.text('NOW()'), nullable=True),
        sa.Column('user_id', sa.Text(), nullable=True),
        sa.Column('api_token_id', sa.Integer(), nullable=True),
        sa.Column('tool_name', sa.Text(), nullable=False),
        sa.Column('params_hash', sa.Text(), nullable=True),
        sa.Column('scope_used', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('result_size_bytes', sa.Integer(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('ip', sa.Text(), nullable=True),
        sa.Column('error_class', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mcp_audit_log_ts', 'mcp_audit_log', [sa.text('ts DESC')])
    op.create_index('ix_mcp_audit_log_user_ts', 'mcp_audit_log', ['user_id', sa.text('ts DESC')])
    op.create_index('ix_mcp_audit_log_tool_ts', 'mcp_audit_log', ['tool_name', sa.text('ts DESC')])


def downgrade():
    op.drop_table('mcp_audit_log')
