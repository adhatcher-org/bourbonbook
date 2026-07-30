"""Add async add-bottle pipeline progress tracking to bottles.

Revision ID: 0009_bottle_processing_stage
Revises: 0008_catalog_import_persistence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_bottle_processing_stage"
down_revision: str | Sequence[str] | None = "0008_catalog_import_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bottles",
        sa.Column("processing_stage", sa.String(length=20), nullable=False, server_default="idle"),
    )
    op.add_column("bottles", sa.Column("processing_error", sa.Text(), nullable=True))
    op.create_index("ix_bottles_processing_stage", "bottles", ["processing_stage"])


def downgrade() -> None:
    op.drop_index("ix_bottles_processing_stage", table_name="bottles")
    op.drop_column("bottles", "processing_error")
    op.drop_column("bottles", "processing_stage")
