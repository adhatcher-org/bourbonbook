"""Durable, single-lane orchestration for local catalog-import extraction."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from bourbonbook.catalog import catalog_price_key
from bourbonbook.catalog_extract import (
    CatalogExtractionError,
    CatalogExtractionProposal,
    extract_catalog_files,
)
from bourbonbook.catalog_imports import CatalogImportState, transition_batch
from bourbonbook.catalog_uploads import (
    catalog_import_batch_directory,
    cleanup_expired_catalog_import_sources,
    remove_catalog_import_batch_sources,
)
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.models import CatalogImportBatch, CatalogImportProposal
from bourbonbook.observability import observe_catalog_import

logger = logging.getLogger(__name__)
MAX_AUTOMATIC_ATTEMPTS = 2  # initial attempt plus one transient retry


class CatalogExtractor(Protocol):
    async def __call__(
        self, paths: Sequence[Path], client: object, settings: Settings, **kwargs: object
    ) -> list[CatalogExtractionProposal]: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def recover_expired_catalog_import_leases(session: Session, now: datetime) -> int:
    """Requeue interrupted work only after its persisted lease has expired."""
    result = session.execute(
        update(CatalogImportBatch)
        .where(
            CatalogImportBatch.state == CatalogImportState.EXTRACTING.value,
            CatalogImportBatch.lease_expires_at.is_not(None),
            CatalogImportBatch.lease_expires_at <= now,
        )
        .values(
            state=CatalogImportState.QUEUED.value,
            lease_expires_at=None,
            updated_at=now,
            error_summary="Interrupted extraction lease recovered.",
        )
    )
    return int(result.rowcount or 0)


def claim_next_catalog_import(
    session: Session, settings: Settings, now: datetime
) -> CatalogImportBatch | None:
    """FIFO claim guarded by a conditional update so two callers cannot run one batch."""
    candidate_id = session.scalar(
        select(CatalogImportBatch.id)
        .where(CatalogImportBatch.state == CatalogImportState.QUEUED.value)
        .order_by(CatalogImportBatch.created_at, CatalogImportBatch.id)
        .limit(1)
    )
    if candidate_id is None:
        return None
    claimed = session.execute(
        update(CatalogImportBatch)
        .where(
            CatalogImportBatch.id == candidate_id,
            CatalogImportBatch.state == CatalogImportState.QUEUED.value,
        )
        .values(
            state=CatalogImportState.EXTRACTING.value,
            attempt_count=CatalogImportBatch.attempt_count + 1,
            lease_expires_at=now + timedelta(seconds=settings.catalog_import_lease_seconds),
            error_summary=None,
            updated_at=now,
        )
    )
    if not claimed.rowcount:
        return None
    return session.get(CatalogImportBatch, candidate_id)


def heartbeat_catalog_import_lease(
    session: Session, batch_id: int, settings: Settings, now: datetime
) -> bool:
    result = session.execute(
        update(CatalogImportBatch)
        .where(
            CatalogImportBatch.id == batch_id,
            CatalogImportBatch.state == CatalogImportState.EXTRACTING.value,
        )
        .values(
            lease_expires_at=now + timedelta(seconds=settings.catalog_import_lease_seconds),
            updated_at=now,
        )
    )
    return bool(result.rowcount)


class CatalogImportWorker:
    """One lifespan-owned worker; extraction never executes in the HTTP request path."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        settings: Settings,
        client: object,
        *,
        extractor: CatalogExtractor = extract_catalog_files,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._client = client
        self._extractor = extractor
        self._now = now
        self._sleep = sleep
        self._lane = asyncio.Semaphore(1)
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        with self._session_factory() as session:
            recovered = recover_expired_catalog_import_leases(session, self._now())
            session.commit()
            # Cleanup must follow lease recovery: interrupted work is requeued and therefore
            # retains its input even when the directory is older than the terminal-source TTL.
            cleanup_expired_catalog_import_sources(self._settings, session)
            session.commit()
        if recovered:
            log_event(
                logger,
                logging.WARNING,
                "catalog_import_leases_recovered",
                "Recovered catalog import leases",
                recovered=recovered,
            )
        self._task = asyncio.create_task(self._run(), name="catalog-import-worker")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stopping.is_set():
            # Run at the worker's poll cadence so terminal and orphan sources can expire without
            # requiring an application restart. The cleanup routine preserves queued/extracting
            # inputs, including batches recovered from an expired lease.
            self._cleanup_expired_sources()
            found = await self.process_next()
            if not found:
                await self._sleep(self._settings.catalog_import_poll_seconds)

    def _cleanup_expired_sources(self) -> None:
        with self._session_factory() as session:
            cleanup_expired_catalog_import_sources(self._settings, session)
            session.commit()

    async def process_next(self) -> bool:
        """Claim and process at most one batch; convenient for deterministic tests."""
        with self._session_factory() as session:
            batch = claim_next_catalog_import(session, self._settings, self._now())
            session.commit()
            if batch is None:
                return False
            batch_id = batch.id
            created_at = batch.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            queue_wait = max(0.0, (self._now() - created_at).total_seconds())

        started = time.perf_counter()
        try:
            async with self._lane:
                proposals = await self._extract_with_heartbeat(batch_id)
        except asyncio.CancelledError:
            # Retain the lease and source; startup recovery will safely requeue it if needed.
            raise
        except BaseException as exc:
            terminal = await self._record_failure(batch_id, exc)
            observe_catalog_import(
                "failed" if terminal else "retry", queue_wait, time.perf_counter() - started
            )
            return True

        self._persist_proposals(batch_id, proposals)
        observe_catalog_import("review", queue_wait, time.perf_counter() - started)
        return True

    async def _extract_with_heartbeat(self, batch_id: int) -> list[CatalogExtractionProposal]:
        paths = sorted(catalog_import_batch_directory(self._settings, batch_id).iterdir())
        extraction = asyncio.create_task(
            self._extractor(
                paths,
                self._client,
                self._settings,
                timeout_seconds=self._settings.catalog_import_chunk_timeout_seconds,
            )
        )
        heartbeat = asyncio.create_task(self._heartbeat_until_done(batch_id, extraction))
        try:
            async with asyncio.timeout(self._settings.catalog_import_batch_timeout_seconds):
                return await extraction
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            if not extraction.done():
                extraction.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await extraction

    async def _heartbeat_until_done(self, batch_id: int, extraction: asyncio.Task[object]) -> None:
        while not extraction.done():
            await self._sleep(self._settings.catalog_import_lease_heartbeat_seconds)
            if extraction.done():
                return
            with self._session_factory() as session:
                heartbeat_catalog_import_lease(session, batch_id, self._settings, self._now())
                session.commit()

    def _persist_proposals(
        self, batch_id: int, proposals: Sequence[CatalogExtractionProposal]
    ) -> None:
        with self._session_factory() as session:
            batch = session.get(CatalogImportBatch, batch_id)
            if batch is None or batch.state != CatalogImportState.EXTRACTING.value:
                return
            session.execute(
                delete(CatalogImportProposal).where(CatalogImportProposal.batch_id == batch_id)
            )
            price_updated_at = batch.requested_price_updated_at or self._now().date()
            session.add_all(
                CatalogImportProposal(
                    batch_id=batch_id,
                    position=position,
                    name=proposal.name[:240],
                    product_key=catalog_price_key(proposal.name, proposal.size)[0],
                    size_key=catalog_price_key(proposal.name, proposal.size)[1],
                    msrp=proposal.msrp,
                    price_updated_at=price_updated_at,
                )
                for position, proposal in enumerate(proposals, start=1)
            )
            transition_batch(batch, CatalogImportState.REVIEW, now=self._now)
            session.commit()
        remove_catalog_import_batch_sources(self._settings, batch_id)
        log_event(
            logger,
            logging.INFO,
            "catalog_import_review_ready",
            "Catalog import proposals ready",
            batch_id=batch_id,
            proposals=len(proposals),
        )

    async def _record_failure(self, batch_id: int, exc: BaseException) -> bool:
        failure_kind = _failure_kind(exc)
        with self._session_factory() as session:
            batch = session.get(CatalogImportBatch, batch_id)
            if batch is None or batch.state != CatalogImportState.EXTRACTING.value:
                return True
            terminal = batch.attempt_count >= MAX_AUTOMATIC_ATTEMPTS or not _is_transient(exc)
            target = CatalogImportState.FAILED if terminal else CatalogImportState.QUEUED
            transition_batch(batch, target, now=self._now)
            batch.error_summary = failure_kind
            session.commit()
        log_event(
            logger,
            logging.WARNING,
            "catalog_import_extraction_failed",
            "Catalog import extraction failed",
            batch_id=batch_id,
            failure_kind=failure_kind,
            terminal=terminal,
        )
        return terminal


def _failure_kind(exc: BaseException) -> str:
    if isinstance(exc, CatalogExtractionError):
        return exc.failure_kind[:40]
    if isinstance(exc, TimeoutError):
        return "timeout"
    return exc.__class__.__name__.lower()[:40]


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or (
        isinstance(exc, CatalogExtractionError) and exc.failure_kind in {"timeout", "transport"}
    )
