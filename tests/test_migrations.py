from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import (
    BASELINE_REVISION,
    HEAD_REVISION,
    MigrationBootstrapError,
    alembic_config,
    bootstrap_database,
)
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
            "api_usage",
            "bottles",
            "price_sources",
            "user_tokens",
            "users",
        }
        bottle_columns = {
            column["name"] for column in inspect(database.engine).get_columns("bottles")
        }
        assert "on_shopping_list" in bottle_columns
        user_columns = {column["name"] for column in inspect(database.engine).get_columns("users")}
        assert {"collection_share_token_hash", "collection_shared_at"} <= user_columns
        assert current_revision(database) == HEAD_REVISION
    finally:
        database.engine.dispose()


def test_legacy_database_is_stamped_without_losing_catalog_data(tmp_path: Path) -> None:
    settings = migration_settings(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    photo = uploads / "legacy-photo.jpg"
    photo.write_bytes(b"existing bottle photo")

    command.upgrade(alembic_config(settings.database_url), BASELINE_REVISION)
    database = Database(settings)
    with database.engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        user_id = connection.execute(
            text(
                "INSERT INTO users (username, display_name, password_hash, created_at) "
                "VALUES ('aaron@example.com', 'Aaron', 'existing-password-hash', CURRENT_TIMESTAMP)"
            )
        ).lastrowid
        bottle_id = connection.execute(
            text(
                "INSERT INTO bottles (owner_id, name, brand, release, edition, spirit_type, "
                "distilled_by, mash_bill, size, age_statement, barrel_number, bottle_number, "
                "warehouse, floor, status, fill_level, quantity, storage_location, rating, "
                "tasting_notes, notes, photo_name, analysis_status, created_at, updated_at) VALUES "
                "(:owner, 'Eagle Rare 10 Year', '', '', '', 'Bourbon', '', '', '750ml', '', '', "
                "'', '', '', 'Unopened', 100, 1, '', 0, '', '', :photo, 'manual', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"owner": user_id, "photo": photo.name},
        ).lastrowid
        connection.execute(
            text(
                "INSERT INTO price_sources "
                "(bottle_id, kind, title, url, basis, checked_at) VALUES "
                "(:bottle, 'msrp', 'Existing source', "
                "'https://example.com/eagle-rare', 'Listed price', CURRENT_TIMESTAMP)"
            ),
            {"bottle": bottle_id},
        )
    database.engine.dispose()

    bootstrap_database(settings)
    bootstrap_database(settings)

    migrated = Database(settings)
    try:
        with migrated.session_factory() as session:
            user = session.get(User, user_id)
            bottle = session.get(Bottle, bottle_id)
            source = session.scalar(select(PriceSource).where(PriceSource.bottle_id == bottle_id))
            assert user is not None
            assert user.username == "aaron@example.com"
            assert user.email == "aaron@example.com"
            assert user.email_verified_at is None
            assert user.password_hash == "existing-password-hash"
            assert bottle is not None
            assert bottle.name == "Eagle Rare 10 Year"
            assert bottle.photo_name == photo.name
            assert source is not None
            assert source.url == "https://example.com/eagle-rare"
        assert photo.read_bytes() == b"existing bottle photo"
        assert current_revision(migrated) == HEAD_REVISION
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
