"""Add shopping-list state to bottles.

Revision ID: 0004_shopping_list
Revises: 0003_api_usage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_shopping_list"
down_revision: str | Sequence[str] | None = "0003_api_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bottles",
        sa.Column("on_shopping_list", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_bottles_on_shopping_list", "bottles", ["on_shopping_list"])


def downgrade() -> None:
    op.drop_index("ix_bottles_on_shopping_list", table_name="bottles")
    op.drop_column("bottles", "on_shopping_list")
