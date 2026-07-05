"""Add user avatars.

Revision ID: 0006_user_avatars
Revises: 0005_collection_sharing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_user_avatars"
down_revision: str | Sequence[str] | None = "0005_collection_sharing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_name")
