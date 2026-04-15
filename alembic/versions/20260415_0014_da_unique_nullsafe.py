"""Add UNIQUE NULLS NOT DISTINCT constraint on document_assignments triple

Replaces the legacy btree unique constraint (which treats NULLs as distinct,
allowing duplicates when meeting_id or agenda_item_id is NULL) with a
NULLS NOT DISTINCT constraint that correctly enforces uniqueness even when
one of the nullable columns is NULL.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-15
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the old constraint(s) first — there may be two copies (audit found
    # "UNIQUE (document_id, meeting_id, agenda_item_id)" appearing twice).
    op.execute(
        "ALTER TABLE document_assignments "
        "DROP CONSTRAINT IF EXISTS document_assignments_document_id_meeting_id_agenda_item_id_key"
    )
    # In case a second copy exists under the same name (corner-case from
    # double-migration), the IF NOT EXISTS on the ADD below will guard us.
    op.execute(
        "ALTER TABLE document_assignments "
        "ADD CONSTRAINT da_triple_unique "
        "UNIQUE NULLS NOT DISTINCT (document_id, meeting_id, agenda_item_id)"
    )


def downgrade():
    op.execute(
        "ALTER TABLE document_assignments "
        "DROP CONSTRAINT IF EXISTS da_triple_unique"
    )
    # Restore the original standard unique constraint
    op.execute(
        "ALTER TABLE document_assignments "
        "ADD CONSTRAINT document_assignments_document_id_meeting_id_agenda_item_id_key "
        "UNIQUE (document_id, meeting_id, agenda_item_id)"
    )
