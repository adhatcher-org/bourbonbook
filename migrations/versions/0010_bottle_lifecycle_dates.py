"""Add optional bottled and purchased dates to bottles.

Revision ID: 0010_bottle_lifecycle_dates
Revises: 0009_bottle_processing_stage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_bottle_lifecycle_dates"
down_revision: str | Sequence[str] | None = "0009_bottle_processing_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic reads these four names off the module itself (alembic/script/base.py
# does getattr(module, "down_revision") and errors when "revision" is absent),
# so they are this module's public contract, not dead assignments.
__all__ = [
    "branch_labels",
    "depends_on",
    "down_revision",
    "downgrade",
    "revision",
    "upgrade",
]


def upgrade() -> None:
    op.add_column("bottles", sa.Column("date_bottled", sa.Date(), nullable=True))
    op.add_column("bottles", sa.Column("date_purchased", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bottles") as batch_op:
        batch_op.drop_column("date_purchased")
        batch_op.drop_column("date_bottled")
