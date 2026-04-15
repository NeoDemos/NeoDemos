"""Add logical unique constraint on meetings (name, start_date::date, committee)

meetings.start_date is a TIMESTAMP WITHOUT TIME ZONE, so we cannot use a
plain ALTER TABLE ... ADD CONSTRAINT UNIQUE — Postgres doesn't allow function
expressions in that form.  We use CREATE UNIQUE INDEX instead, which supports
the ::date cast and NULLS NOT DISTINCT.

The resulting index enforces: no two meetings can share the same name,
calendar date (ignoring time-of-day), and committee value.
NULL committee values are treated as equal (NULLS NOT DISTINCT), so two
meetings with the same name/date and NULL committee will also conflict.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-15
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS meetings_logical_unique
        ON meetings (name, (start_date::date), committee)
        NULLS NOT DISTINCT
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS meetings_logical_unique")
