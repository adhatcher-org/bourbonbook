from __future__ import annotations

import asyncio
from pathlib import Path

from bourbonbook.bottle_processing import (
    BottleProcessingStage,
    recover_orphaned_bottle_processing,
    run_add_bottle_pipeline,
)
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import Bottle, User


def build_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret-that-is-long-enough!",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=10,
        max_upload_mb=2,
    )


def create_user(database: Database, suffix: str = "") -> int:
    with database.session_factory() as session:
        user = User(
            username=f"pipeline-tester{suffix}",
            display_name="Pipeline Tester",
            email=f"pipeline-tester{suffix}@example.com",
            screen_name="Pipeline Tester",
            password_hash="not-used",
        )
        session.add(user)
        session.commit()
        return user.id


def seed_bottle(
    database: Database,
    owner_id: int,
    *,
    name: str = "Untitled bottle",
    processing_stage: str = "queued",
    photo_name: str | None = None,
) -> int:
    with database.session_factory() as session:
        bottle = Bottle(
            owner_id=owner_id,
            name=name,
            processing_stage=processing_stage,
            photo_name=photo_name,
        )
        session.add(bottle)
        session.commit()
        return bottle.id


def test_run_add_bottle_pipeline_commits_each_stage_before_the_next(
    tmp_path: Path, monkeypatch
) -> None:
    configured = build_settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    owner_id = create_user(database)
    bottle_id = seed_bottle(database, owner_id, name="Eagle Rare 10 Year", photo_name="fixture.jpg")

    observed_stages: list[str] = []

    async def fake_analyze_bottle(photo, settings_arg):
        with database.session_factory() as session:
            observed_stages.append(session.get(Bottle, bottle_id).processing_stage)
        return {"brand": "Eagle Rare"}, "complete"

    async def fake_enrich_bottle_by_name(bottle, settings_arg, *, allow_provider=True):
        with database.session_factory() as session:
            observed_stages.append(session.get(Bottle, bottle_id).processing_stage)
        return {}, "unavailable"

    async def fake_apply_user_purchase_price(session, bottle, price_index=None):
        observed_stages.append(bottle.processing_stage)
        return False

    async def fake_refresh_prices(session, bottle, settings_arg, *, force=False, price_index=None):
        observed_stages.append(bottle.processing_stage)
        return "unavailable"

    monkeypatch.setattr("bourbonbook.bottle_processing.analyze_bottle", fake_analyze_bottle)
    monkeypatch.setattr("bourbonbook.main.enrich_bottle_by_name", fake_enrich_bottle_by_name)
    monkeypatch.setattr(
        "bourbonbook.main.apply_user_purchase_price", fake_apply_user_purchase_price
    )
    monkeypatch.setattr("bourbonbook.main.refresh_prices", fake_refresh_prices)

    asyncio.run(
        run_add_bottle_pipeline(
            database.session_factory, bottle_id, configured, None, None, owner_id
        )
    )

    assert observed_stages == ["analyzing", "enriching", "pricing", "pricing"]

    with database.session_factory() as session:
        bottle = session.get(Bottle, bottle_id)
        assert bottle.processing_stage == BottleProcessingStage.COMPLETE.value
        assert bottle.analysis_status == "complete"
        assert bottle.brand == "Eagle Rare"
        assert bottle.processing_error is None


def test_run_add_bottle_pipeline_drops_a_noisy_proof_but_completes_and_keeps_the_name(
    tmp_path: Path, monkeypatch
) -> None:
    """A vision-model response with a noisy, non-numeric ``proof`` (e.g. "107 proof") used to
    raise a SQLAlchemy StatementError at commit time on the Float column, rolling back the whole
    stage -- including the clean ``name`` -- and leaving the bottle stuck failed. The bad field
    should be dropped in favor of ``None`` while everything else still saves and the pipeline
    reaches COMPLETE."""
    configured = build_settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    owner_id = create_user(database)
    bottle_id = seed_bottle(database, owner_id, photo_name="fixture.jpg")

    async def fake_analyze_bottle(photo, settings_arg):
        return {"name": "Eagle Rare 10 Year", "proof": "107 proof"}, "complete"

    async def fake_enrich_bottle_by_name(bottle, settings_arg, *, allow_provider=True):
        return {}, "unavailable"

    async def fake_apply_user_purchase_price(session, bottle, price_index=None):
        return False

    async def fake_refresh_prices(session, bottle, settings_arg, *, force=False, price_index=None):
        return "unavailable"

    monkeypatch.setattr("bourbonbook.bottle_processing.analyze_bottle", fake_analyze_bottle)
    monkeypatch.setattr("bourbonbook.main.enrich_bottle_by_name", fake_enrich_bottle_by_name)
    monkeypatch.setattr(
        "bourbonbook.main.apply_user_purchase_price", fake_apply_user_purchase_price
    )
    monkeypatch.setattr("bourbonbook.main.refresh_prices", fake_refresh_prices)

    asyncio.run(
        run_add_bottle_pipeline(
            database.session_factory, bottle_id, configured, None, None, owner_id
        )
    )

    with database.session_factory() as session:
        bottle = session.get(Bottle, bottle_id)
        assert bottle.processing_stage == BottleProcessingStage.COMPLETE.value
        assert bottle.processing_error is None
        assert bottle.proof is None
        assert bottle.name == "Eagle Rare 10 Year"


def test_run_add_bottle_pipeline_skips_enrichment_and_pricing_for_a_placeholder_name(
    tmp_path: Path, monkeypatch
) -> None:
    configured = build_settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    owner_id = create_user(database)
    bottle_id = seed_bottle(database, owner_id, name="Untitled bottle")

    calls: list[str] = []

    async def fake_analyze_bottle(photo, settings_arg):
        return {}, "unavailable"

    async def fake_enrich_bottle_by_name(bottle, settings_arg, *, allow_provider=True):
        calls.append("enrich")
        return {}, "unavailable"

    async def fake_apply_user_purchase_price(session, bottle, price_index=None):
        calls.append("price")
        return False

    monkeypatch.setattr("bourbonbook.bottle_processing.analyze_bottle", fake_analyze_bottle)
    monkeypatch.setattr("bourbonbook.main.enrich_bottle_by_name", fake_enrich_bottle_by_name)
    monkeypatch.setattr(
        "bourbonbook.main.apply_user_purchase_price", fake_apply_user_purchase_price
    )

    asyncio.run(
        run_add_bottle_pipeline(
            database.session_factory, bottle_id, configured, None, None, owner_id
        )
    )

    assert calls == []
    with database.session_factory() as session:
        bottle = session.get(Bottle, bottle_id)
        assert bottle.processing_stage == BottleProcessingStage.COMPLETE.value


def test_run_add_bottle_pipeline_exception_marks_the_bottle_failed(
    tmp_path: Path, monkeypatch
) -> None:
    configured = build_settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    owner_id = create_user(database)
    bottle_id = seed_bottle(database, owner_id, name="Eagle Rare 10 Year", photo_name="fixture.jpg")

    async def failing_analyze_bottle(photo, settings_arg):
        raise RuntimeError("ollama exploded")

    monkeypatch.setattr("bourbonbook.bottle_processing.analyze_bottle", failing_analyze_bottle)

    asyncio.run(
        run_add_bottle_pipeline(
            database.session_factory, bottle_id, configured, None, None, owner_id
        )
    )

    with database.session_factory() as session:
        bottle = session.get(Bottle, bottle_id)
        assert bottle.processing_stage == BottleProcessingStage.FAILED.value
        assert bottle.processing_error is not None
        assert "ollama exploded" in bottle.processing_error


def test_run_add_bottle_pipeline_returns_quietly_for_a_missing_bottle(tmp_path: Path) -> None:
    configured = build_settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)

    asyncio.run(
        run_add_bottle_pipeline(database.session_factory, 999_999, configured, None, None, 1)
    )
    # No exception, no row created -- nothing more to assert.


def test_recover_orphaned_bottle_processing_flips_in_progress_rows_to_failed(
    tmp_path: Path,
) -> None:
    configured = build_settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    owner_id = create_user(database)
    analyzing_id = seed_bottle(database, owner_id, name="A", processing_stage="analyzing")
    pricing_id = seed_bottle(database, owner_id, name="B", processing_stage="pricing")
    complete_id = seed_bottle(database, owner_id, name="C", processing_stage="complete")
    idle_id = seed_bottle(database, owner_id, name="D", processing_stage="idle")

    with database.session_factory() as session:
        recovered = recover_orphaned_bottle_processing(session)
        session.commit()

    assert recovered == 2

    with database.session_factory() as session:
        assert session.get(Bottle, analyzing_id).processing_stage == "failed"
        assert session.get(Bottle, analyzing_id).processing_error == "Interrupted by server restart"
        assert session.get(Bottle, pricing_id).processing_stage == "failed"
        assert session.get(Bottle, complete_id).processing_stage == "complete"
        assert session.get(Bottle, idle_id).processing_stage == "idle"
