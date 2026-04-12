"""WS2 baseline marker — existing schema managed by legacy scripts.

Revision ID: 0001
Revises: None
Create Date: 2026-04-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing production tables (documents, document_chunks, meetings, etc.)
    # are managed by scripts/create_*.py and scripts/migrate_*.sql.
    # This baseline revision marks the point where Alembic takes over
    # for NEW tables only. See docs/handoffs/WS2_FINANCIAL.md.
    pass


def downgrade() -> None:
    pass
