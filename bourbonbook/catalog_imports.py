"""State rules shared by catalog-import persistence and future worker orchestration."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum

from sqlalchemy import Date, DateTime, func, insert, literal, select, update
from sqlalchemy.orm import Session

from bourbonbook.models import CatalogImportBatch, CatalogImportProposal, CatalogPrice


class CatalogImportState(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    REVIEW = "review"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"


_ALLOWED_TRANSITIONS = {
    CatalogImportState.QUEUED: {
        CatalogImportState.EXTRACTING,
        CatalogImportState.FAILED,
        CatalogImportState.EXPIRED,
    },
    CatalogImportState.EXTRACTING: {
        CatalogImportState.QUEUED,
        CatalogImportState.REVIEW,
        CatalogImportState.FAILED,
    },
    CatalogImportState.REVIEW: {CatalogImportState.APPLIED, CatalogImportState.EXPIRED},
    CatalogImportState.FAILED: {CatalogImportState.QUEUED, CatalogImportState.EXPIRED},
    CatalogImportState.APPLIED: set(),
    CatalogImportState.EXPIRED: set(),
}


@dataclass(frozen=True)
class CatalogImportApplyResult:
    """The durable outcome of applying one reviewed import batch."""

    created: int
    updated: int
    unchanged: int
    skipped: int
    catalog_price_ids: tuple[int, ...]


class CatalogImportApplyStateError(ValueError):
    """Raised when a batch is no longer eligible for catalog application."""


@dataclass(frozen=True)
class CatalogImportReviewUpdate:
    """A validated, page-scoped review edit submitted alongside an apply request."""

    proposal_id: int
    name: str
    product_key: str
    size_key: str
    msrp: float
    price_updated_at: date
    included: bool


def retry_failed_catalog_import_batch(
    session: Session,
    batch_id: int,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> bool:
    """Atomically make one failed batch eligible for a fresh extraction attempt.

    A manual retry starts a new bounded automatic-attempt cycle.  The state predicate means a
    concurrent retry or worker claim cannot make the same failed job runnable twice.
    """
    result = session.execute(
        update(CatalogImportBatch)
        .where(
            CatalogImportBatch.id == batch_id,
            CatalogImportBatch.state == CatalogImportState.FAILED.value,
        )
        .values(
            state=CatalogImportState.QUEUED.value,
            attempt_count=0,
            lease_expires_at=None,
            error_summary=None,
            updated_at=now(),
        )
    )
    return bool(result.rowcount)


def apply_catalog_import_batch(
    session: Session,
    batch_id: int,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    before_persist: Callable[[CatalogImportProposal], None] | None = None,
    review_updates: Sequence[CatalogImportReviewUpdate] = (),
) -> CatalogImportApplyResult:
    """Apply included reviewed proposals in one SQLite transaction.

    This deliberately owns no provider or vector-index work.  A failure while creating or
    updating any price rolls back every price and leaves the batch awaiting review.  The
    returned IDs identify the committed rows a caller may reindex after this transaction.
    """
    with session.begin():
        batch = session.get(CatalogImportBatch, batch_id)
        if batch is None:
            raise LookupError("Catalog import batch not found.")
        if batch.state != CatalogImportState.REVIEW.value:
            raise CatalogImportApplyStateError("Only batches awaiting review can be applied.")

        if review_updates:
            updates_by_id = {update.proposal_id: update for update in review_updates}
            updated_proposals = session.scalars(
                select(CatalogImportProposal).where(
                    CatalogImportProposal.batch_id == batch.id,
                    CatalogImportProposal.id.in_(updates_by_id),
                )
            )
            for proposal in updated_proposals:
                review_update = updates_by_id[proposal.id]
                proposal.name = review_update.name
                proposal.product_key = review_update.product_key
                proposal.size_key = review_update.size_key
                proposal.msrp = review_update.msrp
                proposal.price_updated_at = review_update.price_updated_at
                proposal.included = review_update.included
                proposal.validation_error = None

        proposals = list(
            session.scalars(
                select(CatalogImportProposal)
                .where(CatalogImportProposal.batch_id == batch.id)
                .order_by(CatalogImportProposal.position)
            )
        )
        created = updated = unchanged = skipped = 0
        applied_prices: list[CatalogPrice] = []
        for proposal in proposals:
            if not proposal.included:
                skipped += 1
                continue
            if before_persist is not None:
                before_persist(proposal)
            price = session.scalar(
                select(CatalogPrice).where(
                    CatalogPrice.product_key == proposal.product_key,
                    CatalogPrice.size_key == proposal.size_key,
                )
            )
            if price is None:
                price = CatalogPrice(
                    product_key=proposal.product_key,
                    size_key=proposal.size_key,
                    msrp=proposal.msrp,
                    title="Local screenshot catalog",
                    url="",
                    basis=f"Approved catalog import batch #{batch.id}.",
                    checked_at=datetime.combine(proposal.price_updated_at, time.min, tzinfo=UTC),
                )
                session.add(price)
                created += 1
            else:
                proposal_checked_at = datetime.combine(
                    proposal.price_updated_at, time.min, tzinfo=UTC
                )
                price_checked_at = price.checked_at
                if price_checked_at.tzinfo is None:
                    price_checked_at = price_checked_at.replace(tzinfo=UTC)
                if price_checked_at > proposal_checked_at or (
                    price_checked_at == proposal_checked_at and price.msrp == proposal.msrp
                ):
                    unchanged += 1
                    continue
                price.msrp = proposal.msrp
                price.title = "Local screenshot catalog"
                price.url = ""
                price.basis = f"Approved catalog import batch #{batch.id}."
                price.checked_at = proposal_checked_at
                updated += 1
            applied_prices.append(price)
        # Newly created rows need their primary keys before the committed result can identify
        # the exact subset eligible for a post-commit index refresh.
        session.flush()
        timestamp = now()
        transitioned = session.execute(
            update(CatalogImportBatch)
            .where(
                CatalogImportBatch.id == batch.id,
                CatalogImportBatch.state == CatalogImportState.REVIEW.value,
            )
            .values(
                state=CatalogImportState.APPLIED.value,
                updated_at=timestamp,
                applied_at=timestamp,
                lease_expires_at=None,
            )
        )
        if transitioned.rowcount != 1:
            raise CatalogImportApplyStateError("Only batches awaiting review can be applied.")
    return CatalogImportApplyResult(
        created=created,
        updated=updated,
        unchanged=unchanged,
        skipped=skipped,
        catalog_price_ids=tuple(dict.fromkeys(price.id for price in applied_prices)),
    )


def transition_batch(
    batch: CatalogImportBatch,
    target: CatalogImportState,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Move a batch through its durable lifecycle or reject an invalid transition."""
    current = CatalogImportState(batch.state)
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Cannot transition catalog import batch from {current} to {target}")

    batch.state = target.value
    batch.updated_at = now()
    if target is CatalogImportState.APPLIED:
        batch.applied_at = batch.updated_at
    if target is not CatalogImportState.EXTRACTING:
        batch.lease_expires_at = None


def reserve_catalog_import_batch(
    session: Session,
    *,
    created_by_user_id: int,
    requested_price_updated_at: date | None,
    source_file_count: int,
    queue_capacity: int,
    now: datetime | None = None,
) -> int | None:
    """Atomically reserve a queued-batch slot or return ``None`` when it is full.

    The count predicate lives in the same INSERT statement as the reservation, so separate
    request sessions cannot both observe a free final slot and over-admit work.
    """
    timestamp = now or datetime.now(UTC)
    queued_count = (
        select(func.count(CatalogImportBatch.id))
        .where(CatalogImportBatch.state == CatalogImportState.QUEUED.value)
        .scalar_subquery()
    )
    statement = (
        insert(CatalogImportBatch)
        .from_select(
            [
                CatalogImportBatch.created_by_user_id,
                CatalogImportBatch.state,
                CatalogImportBatch.requested_price_updated_at,
                CatalogImportBatch.source_file_count,
                CatalogImportBatch.attempt_count,
                CatalogImportBatch.created_at,
                CatalogImportBatch.updated_at,
            ],
            select(
                literal(created_by_user_id),
                literal(CatalogImportState.QUEUED.value),
                literal(requested_price_updated_at, type_=Date()),
                literal(source_file_count),
                literal(0),
                literal(timestamp, type_=DateTime(timezone=True)),
                literal(timestamp, type_=DateTime(timezone=True)),
            ).where(queued_count < queue_capacity),
        )
        .returning(CatalogImportBatch.id)
    )
    return session.scalar(statement)
