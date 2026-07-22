from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from bourbonbook.catalog_extract import CatalogExtractionError, CatalogExtractionProposal
from bourbonbook.catalog_import_worker import (
    CatalogImportWorker,
    claim_next_catalog_import,
    recover_expired_catalog_import_leases,
)
from bourbonbook.catalog_imports import (
    CatalogImportState,
    reserve_catalog_import_batch,
    retry_failed_catalog_import_batch,
)
from bourbonbook.catalog_uploads import (
    catalog_import_batch_directory,
    cleanup_expired_catalog_import_sources,
    remove_catalog_import_batch_sources,
)
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import CatalogImportBatch, CatalogImportProposal, User


def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=1,
        max_upload_mb=2,
        catalog_import_poll_seconds=3600,
    )


def create_batch(database: Database, configured: Settings, *, state: str = "queued") -> int:
    with database.session_factory() as session:
        user = session.query(User).filter_by(email="admin@example.com").one_or_none()
        if user is None:
            user = User(
                username="admin@example.com",
                display_name="Admin",
                email="admin@example.com",
                screen_name="Admin",
                password_hash="not-used",
                is_admin=True,
            )
            session.add(user)
            session.flush()
        batch = CatalogImportBatch(created_by_user_id=user.id, state=state, source_file_count=1)
        session.add(batch)
        session.commit()
        batch_id = batch.id
    source_directory = catalog_import_batch_directory(configured, batch_id)
    source_directory.mkdir(parents=True)
    (source_directory / "source.png").write_bytes(b"fixture")
    return batch_id


def test_claim_is_fifo_and_cannot_duplicate_a_conditional_claim(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    first = create_batch(database, configured)
    second = create_batch(database, configured)
    now = datetime(2026, 7, 22, tzinfo=UTC)

    with database.session_factory() as session:
        claimed = claim_next_catalog_import(session, configured, now)
        session.commit()
        assert claimed is not None
        assert claimed.id == first
        duplicate = claim_next_catalog_import(session, configured, now)
        session.commit()

    assert duplicate is not None
    assert duplicate.id == second
    with database.session_factory() as session:
        assert claim_next_catalog_import(session, configured, now) is None


def test_independent_sessions_racing_for_one_batch_only_claim_once(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured)
    barrier = Barrier(2)
    now = datetime(2026, 7, 22, tzinfo=UTC)

    def claim_in_own_session() -> int | None:
        with database.session_factory() as session:
            barrier.wait()
            claimed = claim_next_catalog_import(session, configured, now)
            session.commit()
            return claimed.id if claimed is not None else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _ignored: claim_in_own_session(), range(2)))

    assert claims.count(batch_id) == 1
    assert claims.count(None) == 1


def test_independent_sessions_cannot_over_admit_capacity_one_queue(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    create_batch(database, configured, state=CatalogImportState.REVIEW.value)
    barrier = Barrier(2)
    now = datetime(2026, 7, 22, tzinfo=UTC)

    def reserve_in_own_session() -> int | None:
        with database.session_factory() as session:
            admin = session.query(User).filter_by(email="admin@example.com").one()
            barrier.wait()
            batch_id = reserve_catalog_import_batch(
                session,
                created_by_user_id=admin.id,
                requested_price_updated_at=None,
                source_file_count=1,
                queue_capacity=1,
                now=now,
            )
            session.commit()
            return batch_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = list(executor.map(lambda _ignored: reserve_in_own_session(), range(2)))

    assert sum(batch_id is not None for batch_id in reservations) == 1
    with database.session_factory() as session:
        assert session.query(CatalogImportBatch).filter_by(state="queued").count() == 1


def test_worker_persists_proposals_then_moves_to_review_and_removes_sources(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured)

    async def successful_extractor(*_args, **_kwargs) -> list[CatalogExtractionProposal]:
        return [CatalogExtractionProposal(name="Example Bourbon", size="750ML", msrp=42.50)]

    worker = CatalogImportWorker(
        database.session_factory, configured, object(), extractor=successful_extractor
    )
    assert asyncio.run(worker.process_next())

    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        proposal = session.query(CatalogImportProposal).filter_by(batch_id=batch_id).one()
        assert batch is not None
        assert (batch.state, batch.attempt_count, batch.lease_expires_at) == ("review", 1, None)
        assert (proposal.product_key, proposal.size_key, proposal.msrp) == (
            "example bourbon",
            "750ml",
            42.50,
        )
    assert not catalog_import_batch_directory(configured, batch_id).exists()


def test_worker_retries_only_one_transient_failure_then_retains_sources_for_admin_retry(
    tmp_path: Path,
) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured)

    async def failing_extractor(*_args, **_kwargs) -> list[CatalogExtractionProposal]:
        raise CatalogExtractionError("transport")

    worker = CatalogImportWorker(
        database.session_factory, configured, object(), extractor=failing_extractor
    )
    assert asyncio.run(worker.process_next())
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        assert (batch.state, batch.attempt_count, batch.error_summary) == ("queued", 1, "transport")
    assert catalog_import_batch_directory(configured, batch_id).exists()

    assert asyncio.run(worker.process_next())
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        assert (batch.state, batch.attempt_count, batch.error_summary) == ("failed", 2, "transport")
    assert catalog_import_batch_directory(configured, batch_id).exists()


def test_concurrent_manual_retries_only_requeue_one_failed_batch(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured, state=CatalogImportState.FAILED.value)
    barrier = Barrier(2)
    now = datetime(2026, 7, 22, tzinfo=UTC)

    def retry_in_own_session() -> bool:
        with database.session_factory() as session:
            barrier.wait()
            retried = retry_failed_catalog_import_batch(session, batch_id, now=lambda: now)
            session.commit()
            return retried

    with ThreadPoolExecutor(max_workers=2) as executor:
        retries = list(executor.map(lambda _ignored: retry_in_own_session(), range(2)))

    assert retries.count(True) == 1
    assert retries.count(False) == 1
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        assert (batch.state, batch.attempt_count, batch.error_summary, batch.lease_expires_at) == (
            "queued",
            0,
            None,
            None,
        )
        claimed = claim_next_catalog_import(session, configured, now)
        session.commit()
        assert claimed is not None and claimed.id == batch_id
        assert claim_next_catalog_import(session, configured, now) is None


def test_worker_cancellation_keeps_leased_source_for_safe_startup_recovery(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured)

    async def blocked_extractor(*_args, **_kwargs) -> list[CatalogExtractionProposal]:
        await asyncio.Event().wait()
        return []

    worker = CatalogImportWorker(
        database.session_factory, configured, object(), extractor=blocked_extractor
    )

    async def cancel_processing() -> None:
        processing = asyncio.create_task(worker.process_next())
        await asyncio.sleep(0)
        processing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await processing

    asyncio.run(cancel_processing())
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        assert batch.state == "extracting"
        assert batch.lease_expires_at is not None
    assert catalog_import_batch_directory(configured, batch_id).exists()


def test_lifespan_startup_recovers_leases_and_stop_cancels_its_idle_loop(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured, state=CatalogImportState.EXTRACTING.value)
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        batch.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    worker = CatalogImportWorker(database.session_factory, configured, object())

    async def start_then_stop() -> None:
        await worker.start()
        with database.session_factory() as session:
            batch = session.get(CatalogImportBatch, batch_id)
            assert batch is not None
            assert batch.state == "queued"
        await worker.stop()

    asyncio.run(start_then_stop())


def test_worker_periodically_expires_terminal_sources_without_restart(
    tmp_path: Path,
) -> None:
    configured = Settings(
        **{
            **vars(settings(tmp_path)),
            "catalog_import_poll_seconds": 0.01,
            "catalog_import_source_expiry_hours": 1,
        }
    )
    bootstrap_database(configured)
    database = Database(configured)

    polling = asyncio.Event()
    release_poll = asyncio.Event()
    extracting = asyncio.Event()
    sleep_calls = 0

    async def controlled_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            polling.set()
            await release_poll.wait()
            return
        await asyncio.Event().wait()

    async def blocked_extractor(*_args, **_kwargs) -> list[CatalogExtractionProposal]:
        extracting.set()
        await asyncio.Event().wait()
        return []

    worker = CatalogImportWorker(
        database.session_factory,
        configured,
        object(),
        extractor=blocked_extractor,
        sleep=controlled_sleep,
    )

    async def run_periodic_cleanup() -> None:
        await worker.start()
        await polling.wait()
        failed_id = create_batch(database, configured, state=CatalogImportState.FAILED.value)
        queued_id = create_batch(database, configured, state=CatalogImportState.QUEUED.value)
        extracting_id = create_batch(
            database, configured, state=CatalogImportState.EXTRACTING.value
        )
        old_timestamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
        for batch_id in (failed_id, queued_id, extracting_id):
            source_directory = catalog_import_batch_directory(configured, batch_id)
            os.utime(source_directory, (old_timestamp, old_timestamp))
        release_poll.set()
        await extracting.wait()
        assert not catalog_import_batch_directory(configured, failed_id).exists()
        # The queued batch was claimed after cleanup; both it and independently extracting work
        # still retain their sources through the periodic terminal-source expiry pass.
        assert catalog_import_batch_directory(configured, queued_id).exists()
        assert catalog_import_batch_directory(configured, extracting_id).exists()
        await worker.stop()

    asyncio.run(run_periodic_cleanup())


def test_worker_heartbeats_while_extraction_is_running(tmp_path: Path) -> None:
    configured = Settings(
        **{**vars(settings(tmp_path)), "catalog_import_lease_heartbeat_seconds": 0.01}
    )
    bootstrap_database(configured)
    database = Database(configured)
    create_batch(database, configured)
    release = asyncio.Event()

    async def slow_extractor(*_args, **_kwargs) -> list[CatalogExtractionProposal]:
        await release.wait()
        return []

    worker = CatalogImportWorker(
        database.session_factory, configured, object(), extractor=slow_extractor
    )

    async def process_with_heartbeat() -> None:
        processing = asyncio.create_task(worker.process_next())
        await asyncio.sleep(0.03)
        release.set()
        assert await processing

    asyncio.run(process_with_heartbeat())


def test_worker_fails_missing_sources_without_retry(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured)
    remove_catalog_import_batch_sources(configured, batch_id)
    worker = CatalogImportWorker(database.session_factory, configured, object())

    assert asyncio.run(worker.process_next())
    assert not asyncio.run(worker.process_next())
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        assert (batch.state, batch.error_summary) == ("failed", "filenotfounderror")


def test_expired_extracting_lease_requeues_only_expired_work(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    expired_id = create_batch(database, configured, state=CatalogImportState.EXTRACTING.value)
    active_id = create_batch(database, configured, state=CatalogImportState.EXTRACTING.value)
    now = datetime(2026, 7, 22, tzinfo=UTC)
    with database.session_factory() as session:
        session.get(CatalogImportBatch, expired_id).lease_expires_at = now - timedelta(seconds=1)
        session.get(CatalogImportBatch, active_id).lease_expires_at = now + timedelta(seconds=1)
        session.commit()
    with database.session_factory() as session:
        assert recover_expired_catalog_import_leases(session, now) == 1
        session.commit()
    with database.session_factory() as session:
        expired = session.get(CatalogImportBatch, expired_id)
        active = session.get(CatalogImportBatch, active_id)
        assert expired is not None and active is not None
        assert expired.state == "queued"
        assert expired.lease_expires_at is None
        assert active.state == "extracting"


def test_recovered_expired_lease_retains_old_sources_through_startup_cleanup(
    tmp_path: Path,
) -> None:
    configured = Settings(**{**vars(settings(tmp_path)), "catalog_import_source_expiry_hours": 1})
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, configured, state=CatalogImportState.EXTRACTING.value)
    source = catalog_import_batch_directory(configured, batch_id)
    old = datetime.now(UTC) - timedelta(hours=2)
    source_file = source / "source.png"
    source_file.write_bytes(b"fixture")

    timestamp = old.timestamp()
    os.utime(source, (timestamp, timestamp))
    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        batch.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with database.session_factory() as session:
        assert recover_expired_catalog_import_leases(session, datetime.now(UTC)) == 1
        session.commit()
        cleanup_expired_catalog_import_sources(configured, session)

    with database.session_factory() as session:
        batch = session.get(CatalogImportBatch, batch_id)
        assert batch is not None
        assert batch.state == CatalogImportState.QUEUED.value
    assert source_file.exists()
