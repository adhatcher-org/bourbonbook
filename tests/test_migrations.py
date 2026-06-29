from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import BASELINE_REVISION, MigrationBootstrapError, bootstrap_database
from bourbonbook.models import Bottle, PriceSource, User


def migration_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'bourbonbook.db'}",
        session_secret="migration-test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=10,
        max_upload_mb=2,
    )


def current_revision(database: Database) -> str:
    with database.engine.connect() as connection:
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def test_fresh_database_reaches_head_and_bootstrap_is_idempotent(tmp_path: Path) -> None:
    settings = migration_settings(tmp_path)

    bootstrap_database(settings)
    bootstrap_database(settings)

    database = Database(settings)
    try:
        assert set(inspect(database.engine).get_table_names()) == {
            "alembic_version",
            "bottles",
            "price_sources",
            "users",
        }
        assert current_revision(database) == BASELINE_REVISION
    finally:
        database.engine.dispose()


def test_legacy_database_is_stamped_without_losing_catalog_data(tmp_path: Path) -> None:
    settings = migration_settings(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    photo = uploads / "legacy-photo.jpg"
    photo.write_bytes(b"existing bottle photo")

    database = Database(settings)
    database.create_all()
    with database.session_factory() as session:
        user = User(
            username="aaron",
            display_name="Aaron",
            password_hash="existing-password-hash",
        )
        bottle = Bottle(
            owner=user,
            name="Eagle Rare 10 Year",
            photo_name=photo.name,
        )
        bottle.price_sources.append(
            PriceSource(
                kind="msrp",
                title="Existing source",
                url="https://example.com/eagle-rare",
                basis="Listed price",
            )
        )
        session.add(user)
        session.commit()
        user_id = user.id
        bottle_id = bottle.id
    database.engine.dispose()

    bootstrap_database(settings)
    bootstrap_database(settings)

    migrated = Database(settings)
    try:
        with migrated.session_factory() as session:
            user = session.get(User, user_id)
            bottle = session.get(Bottle, bottle_id)
            source = session.scalar(
                select(PriceSource).where(PriceSource.bottle_id == bottle_id)
            )
            assert user is not None
            assert user.username == "aaron"
            assert user.password_hash == "existing-password-hash"
            assert bottle is not None
            assert bottle.name == "Eagle Rare 10 Year"
            assert bottle.photo_name == photo.name
            assert source is not None
            assert source.url == "https://example.com/eagle-rare"
        assert photo.read_bytes() == b"existing bottle photo"
        assert current_revision(migrated) == BASELINE_REVISION
    finally:
        migrated.engine.dispose()


def test_unrecognized_partial_schema_stops_with_actionable_error(tmp_path: Path) -> None:
    settings = migration_settings(tmp_path)
    database = Database(settings)
    with database.engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    database.engine.dispose()

    with pytest.raises(MigrationBootstrapError, match="Refusing to migrate") as error:
        bootstrap_database(settings)

    assert "missing tables: bottles, price_sources" in str(error.value)
    assert "users missing columns" in str(error.value)


def test_sqlite_connections_enforce_foreign_keys(tmp_path: Path) -> None:
    settings = migration_settings(tmp_path)
    bootstrap_database(settings)
    database = Database(settings)
    try:
        with database.engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        with pytest.raises(IntegrityError), database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bottles "
                    "(owner_id, name, brand, release, edition, spirit_type, distilled_by, "
                    "mash_bill, size, age_statement, barrel_number, bottle_number, warehouse, "
                    "floor, status, fill_level, quantity, storage_location, rating, tasting_notes, "
                    "notes, analysis_status, created_at, updated_at) VALUES "
                    "(999, 'Missing owner', '', '', '', 'Bourbon', '', '', '750ml', '', '', '', "
                    "'', '', 'Unopened', 100, 1, '', 0, '', '', 'manual', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                )
            )
    finally:
        database.engine.dispose()
