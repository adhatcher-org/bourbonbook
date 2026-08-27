"""Add source-grounded product attribution facts and bottle provenance.

Revision ID: 0011_product_attributions
Revises: 0010_bottle_lifecycle_dates
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_product_attributions"
down_revision: str | Sequence[str] | None = "0010_bottle_lifecycle_dates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_attribution_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_key", sa.String(length=500), nullable=False),
        sa.Column("field", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=240)),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=240)),
        sa.Column("url", sa.Text()),
        sa.Column("basis", sa.String(length=500)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_key", "field"),
    )
    op.create_index(
        "ix_product_attribution_facts_product_key", "product_attribution_facts", ["product_key"]
    )
    op.create_table(
        "bottle_attribution_provenance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bottle_id",
            sa.Integer(),
            sa.ForeignKey("bottles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field", sa.String(length=20), nullable=False),
        sa.Column("authority", sa.String(length=20), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "fact_id",
            sa.Integer(),
            sa.ForeignKey("product_attribution_facts.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("bottle_id", "field"),
    )
    op.create_index(
        "ix_bottle_attribution_provenance_bottle_id", "bottle_attribution_provenance", ["bottle_id"]
    )
    op.create_index(
        "ix_bottle_attribution_provenance_fact_id", "bottle_attribution_provenance", ["fact_id"]
    )
    op.execute("""
        INSERT INTO bottle_attribution_provenance (bottle_id, field, authority, observed_at)
        SELECT id, 'distilled_by', 'legacy_unknown', CURRENT_TIMESTAMP FROM bottles
        WHERE trim(distilled_by) <> ''
        UNION ALL
        SELECT id, 'mash_bill', 'legacy_unknown', CURRENT_TIMESTAMP FROM bottles
        WHERE trim(mash_bill) <> ''
    """)


def downgrade() -> None:
    op.drop_index(
        "ix_bottle_attribution_provenance_fact_id", table_name="bottle_attribution_provenance"
    )
    op.drop_index(
        "ix_bottle_attribution_provenance_bottle_id", table_name="bottle_attribution_provenance"
    )
    op.drop_table("bottle_attribution_provenance")
    op.drop_index(
        "ix_product_attribution_facts_product_key", table_name="product_attribution_facts"
    )
    op.drop_table("product_attribution_facts")
