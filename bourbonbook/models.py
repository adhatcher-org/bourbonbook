from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bourbonbook.database import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(254), unique=True, index=True)
    screen_name: Mapped[str] = mapped_column(String(80), default="")
    avatar_name: Mapped[str | None] = mapped_column(String(80))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    collection_share_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True
    )
    collection_shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    bottles: Mapped[list[Bottle]] = relationship(back_populates="owner", cascade="all, delete")
    tokens: Mapped[list[UserToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    catalog_import_batches: Mapped[list[CatalogImportBatch]] = relationship(
        back_populates="created_by", cascade="all, delete-orphan"
    )


class UserToken(Base):
    __tablename__ = "user_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(30), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email_snapshot: Mapped[str] = mapped_column(String(254))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64))
    user: Mapped[User] = relationship(back_populates="tokens")


class Bottle(Base):
    __tablename__ = "bottles"
    __table_args__ = (UniqueConstraint("owner_id", "photo_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(180), default="Untitled bottle")
    brand: Mapped[str] = mapped_column(String(120), default="")
    release: Mapped[str] = mapped_column(String(180), default="")
    edition: Mapped[str] = mapped_column(String(120), default="")
    spirit_type: Mapped[str] = mapped_column(String(80), default="Bourbon")
    distilled_by: Mapped[str] = mapped_column(String(180), default="")
    mash_bill: Mapped[str] = mapped_column(String(240), default="")
    proof: Mapped[float | None] = mapped_column(Float)
    abv: Mapped[float | None] = mapped_column(Float)
    size: Mapped[str] = mapped_column(String(40), default="750ml")
    age_statement: Mapped[str] = mapped_column(String(80), default="")
    barrel_number: Mapped[str] = mapped_column(String(80), default="")
    bottle_number: Mapped[str] = mapped_column(String(80), default="")
    warehouse: Mapped[str] = mapped_column(String(80), default="")
    floor: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="Unopened")
    on_shopping_list: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    fill_level: Mapped[int] = mapped_column(Integer, default=100)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    storage_location: Mapped[str] = mapped_column(String(180), default="")
    purchase_price: Mapped[float | None] = mapped_column(Float)
    msrp: Mapped[float | None] = mapped_column(Float)
    secondary_price: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float] = mapped_column(Float, default=0)
    tasting_notes: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    photo_name: Mapped[str | None] = mapped_column(String(80))
    analysis_status: Mapped[str] = mapped_column(String(30), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    owner: Mapped[User] = relationship(back_populates="bottles")
    price_sources: Mapped[list[PriceSource]] = relationship(
        back_populates="bottle", cascade="all, delete-orphan", order_by="PriceSource.kind"
    )

    @property
    def estimated_value(self) -> float:
        unit_value = self.msrp or self.purchase_price or 0
        return unit_value * self.quantity


class PriceSource(Base):
    __tablename__ = "price_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    bottle_id: Mapped[int] = mapped_column(ForeignKey("bottles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(240), default="")
    url: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    bottle: Mapped[Bottle] = relationship(back_populates="price_sources")


class CatalogPrice(Base):
    """A reusable local MSRP cache shared by all bottles of the same product and size."""

    __tablename__ = "catalog_prices"
    __table_args__ = (UniqueConstraint("product_key", "size_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_key: Mapped[str] = mapped_column(String(240), index=True)
    size_key: Mapped[str] = mapped_column(String(80), default="")
    msrp: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(240), default="OHLQ")
    url: Mapped[str] = mapped_column(Text)
    basis: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CatalogImportBatch(Base):
    """A durable, review-first local catalog import job and its audit state."""

    __tablename__ = "catalog_import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    requested_price_updated_at: Mapped[date | None] = mapped_column(Date)
    source_file_count: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[User] = relationship(back_populates="catalog_import_batches")
    proposals: Mapped[list[CatalogImportProposal]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="CatalogImportProposal.position",
    )


class CatalogImportProposal(Base):
    """One editable extracted catalog-price proposal; it never represents a user bottle."""

    __tablename__ = "catalog_import_proposals"
    __table_args__ = (UniqueConstraint("batch_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_import_batches.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    name: Mapped[str] = mapped_column(String(240))
    product_key: Mapped[str] = mapped_column(String(240), index=True)
    size_key: Mapped[str] = mapped_column(String(80))
    msrp: Mapped[float] = mapped_column(Float)
    price_updated_at: Mapped[date] = mapped_column(Date)
    validation_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )
    batch: Mapped[CatalogImportBatch] = relationship(back_populates="proposals")


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    operation: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    error_type: Mapped[str | None] = mapped_column(String(40))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    web_search_calls: Mapped[int | None] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )
