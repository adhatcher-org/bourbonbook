"""Add durable SQLite-backed catalog-import batches and proposals.

Revision ID: 0008_catalog_import_persistence
Revises: 0007_catalog_prices
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_catalog_import_persistence"
down_revision: str | Sequence[str] | None = "0007_catalog_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("requested_price_updated_at", sa.Date(), nullable=True),
        sa.Column("source_file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_import_batches_created_by_user_id",
        "catalog_import_batches",
        ["created_by_user_id"],
    )
    op.create_index("ix_catalog_import_batches_state", "catalog_import_batches", ["state"])
    op.create_index(
        "ix_catalog_import_batches_lease_expires_at",
        "catalog_import_batches",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_catalog_import_batches_created_at", "catalog_import_batches", ["created_at"]
    )
    op.create_table(
        "catalog_import_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("product_key", sa.String(length=240), nullable=False),
        sa.Column("size_key", sa.String(length=80), nullable=False),
        sa.Column("msrp", sa.Float(), nullable=False),
        sa.Column("price_updated_at", sa.Date(), nullable=False),
        sa.Column("validation_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["catalog_import_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "position"),
    )
    op.create_index(
        "ix_catalog_import_proposals_batch_id", "catalog_import_proposals", ["batch_id"]
    )
    op.create_index(
        "ix_catalog_import_proposals_product_key", "catalog_import_proposals", ["product_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_import_proposals_product_key", table_name="catalog_import_proposals")
    op.drop_index("ix_catalog_import_proposals_batch_id", table_name="catalog_import_proposals")
    op.drop_table("catalog_import_proposals")
    op.drop_index("ix_catalog_import_batches_created_at", table_name="catalog_import_batches")
    op.drop_index("ix_catalog_import_batches_lease_expires_at", table_name="catalog_import_batches")
    op.drop_index("ix_catalog_import_batches_state", table_name="catalog_import_batches")
    op.drop_index(
        "ix_catalog_import_batches_created_by_user_id", table_name="catalog_import_batches"
    )
    op.drop_table("catalog_import_batches")
