"""Baseline the current Bourbon Book schema.

Revision ID: 0001_current_schema
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_current_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_table(
        "bottles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("brand", sa.String(length=120), nullable=False),
        sa.Column("release", sa.String(length=180), nullable=False),
        sa.Column("edition", sa.String(length=120), nullable=False),
        sa.Column("spirit_type", sa.String(length=80), nullable=False),
        sa.Column("distilled_by", sa.String(length=180), nullable=False),
        sa.Column("mash_bill", sa.String(length=240), nullable=False),
        sa.Column("proof", sa.Float(), nullable=True),
        sa.Column("abv", sa.Float(), nullable=True),
        sa.Column("size", sa.String(length=40), nullable=False),
        sa.Column("age_statement", sa.String(length=80), nullable=False),
        sa.Column("barrel_number", sa.String(length=80), nullable=False),
        sa.Column("bottle_number", sa.String(length=80), nullable=False),
        sa.Column("warehouse", sa.String(length=80), nullable=False),
        sa.Column("floor", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("fill_level", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("storage_location", sa.String(length=180), nullable=False),
        sa.Column("purchase_price", sa.Float(), nullable=True),
        sa.Column("msrp", sa.Float(), nullable=True),
        sa.Column("secondary_price", sa.Float(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("tasting_notes", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("photo_name", sa.String(length=80), nullable=True),
        sa.Column("analysis_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "photo_name"),
    )
    op.create_index("ix_bottles_owner_id", "bottles", ["owner_id"], unique=False)
    op.create_table(
        "price_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bottle_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bottle_id"], ["bottles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_sources_bottle_id", "price_sources", ["bottle_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_price_sources_bottle_id", table_name="price_sources")
    op.drop_table("price_sources")
    op.drop_index("ix_bottles_owner_id", table_name="bottles")
    op.drop_table("bottles")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
