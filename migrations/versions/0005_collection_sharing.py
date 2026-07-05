"""Add revocable collection sharing.

Revision ID: 0005_collection_sharing
Revises: 0004_shopping_list
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_collection_sharing"
down_revision: str | Sequence[str] | None = "0004_shopping_list"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("collection_share_token_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "users", sa.Column("collection_shared_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_users_collection_share_token_hash",
        "users",
        ["collection_share_token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_collection_share_token_hash", table_name="users")
    op.drop_column("users", "collection_shared_at")
    op.drop_column("users", "collection_share_token_hash")
