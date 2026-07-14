"""Add an OHLQ-backed local MSRP cache.

Revision ID: 0007_catalog_prices
Revises: 0006_user_avatars
"""

from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

from bourbonbook.catalog import catalog_price_key

revision: str = "0007_catalog_prices"
down_revision: str | Sequence[str] | None = "0006_user_avatars"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_key", sa.String(length=240), nullable=False),
        sa.Column("size_key", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("msrp", sa.Float(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False, server_default="OHLQ"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_key", "size_key"),
    )
    op.create_index("ix_catalog_prices_product_key", "catalog_prices", ["product_key"])
    _backfill_ohlq_prices()


def _is_ohlq_url(value: str) -> bool:
    host = urlsplit(value).hostname or ""
    return host == "ohlq.com" or host.endswith(".ohlq.com")


def _backfill_ohlq_prices() -> None:
    """Preserve existing OHLQ evidence as shared cache entries during the upgrade."""
    bind = op.get_bind()
    bottles = sa.table(
        "bottles",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("size", sa.String),
        sa.column("msrp", sa.Float),
    )
    sources = sa.table(
        "price_sources",
        sa.column("bottle_id", sa.Integer),
        sa.column("kind", sa.String),
        sa.column("title", sa.String),
        sa.column("url", sa.Text),
        sa.column("basis", sa.Text),
        sa.column("checked_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(
            bottles.c.name,
            bottles.c.size,
            bottles.c.msrp,
            sources.c.title,
            sources.c.url,
            sources.c.basis,
            sources.c.checked_at,
        )
        .select_from(bottles.join(sources, bottles.c.id == sources.c.bottle_id))
        .where(sources.c.kind == "msrp")
        .order_by(sources.c.checked_at.desc())
    ).mappings()
    cache = sa.table(
        "catalog_prices",
        sa.column("product_key", sa.String),
        sa.column("size_key", sa.String),
        sa.column("msrp", sa.Float),
        sa.column("title", sa.String),
        sa.column("url", sa.Text),
        sa.column("basis", sa.Text),
        sa.column("checked_at", sa.DateTime(timezone=True)),
    )
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if row["msrp"] is None or not _is_ohlq_url(row["url"]):
            continue
        key = catalog_price_key(row["name"], row["size"])
        if not key[0] or key in seen:
            continue
        bind.execute(
            cache.insert().values(
                product_key=key[0],
                size_key=key[1],
                msrp=row["msrp"],
                title=row["title"] or "OHLQ",
                url=row["url"],
                basis=row["basis"] or "",
                checked_at=row["checked_at"],
            )
        )
        seen.add(key)


def downgrade() -> None:
    op.drop_index("ix_catalog_prices_product_key", table_name="catalog_prices")
    op.drop_table("catalog_prices")
