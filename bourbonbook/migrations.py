from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from bourbonbook.config import Settings
from bourbonbook.database import create_database_engine

logger = logging.getLogger(__name__)

BASELINE_REVISION = "0001_current_schema"
HEAD_REVISION = "0008_catalog_import_persistence"
EXPECTED_SCHEMA = {
    "users": {
        "id",
        "username",
        "display_name",
        "password_hash",
        "created_at",
    },
    "bottles": {
        "id",
        "owner_id",
        "name",
        "brand",
        "release",
        "edition",
        "spirit_type",
        "distilled_by",
        "mash_bill",
        "proof",
        "abv",
        "size",
        "age_statement",
        "barrel_number",
        "bottle_number",
        "warehouse",
        "floor",
        "status",
        "fill_level",
        "quantity",
        "storage_location",
        "purchase_price",
        "msrp",
        "secondary_price",
        "rating",
        "tasting_notes",
        "notes",
        "photo_name",
        "analysis_status",
        "created_at",
        "updated_at",
    },
    "price_sources": {
        "id",
        "bottle_id",
        "kind",
        "title",
        "url",
        "basis",
        "checked_at",
    },
}


class MigrationBootstrapError(RuntimeError):
    """Raised when an unversioned database cannot be safely identified."""


def alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    config = Config(root / "alembic.ini")
    config.set_main_option("script_location", str(root / "migrations"))
    config.attributes["database_url"] = database_url
    return config


def _legacy_schema_error(actual_tables: set[str], inspector) -> MigrationBootstrapError:
    expected_tables = set(EXPECTED_SCHEMA)
    details: list[str] = []
    missing_tables = expected_tables - actual_tables
    unexpected_tables = actual_tables - expected_tables
    if missing_tables:
        details.append(f"missing tables: {', '.join(sorted(missing_tables))}")
    if unexpected_tables:
        details.append(f"unexpected tables: {', '.join(sorted(unexpected_tables))}")

    for table in sorted(expected_tables & actual_tables):
        actual_columns = {column["name"] for column in inspector.get_columns(table)}
        missing_columns = EXPECTED_SCHEMA[table] - actual_columns
        unexpected_columns = actual_columns - EXPECTED_SCHEMA[table]
        if missing_columns:
            details.append(f"{table} missing columns: {', '.join(sorted(missing_columns))}")
        if unexpected_columns:
            details.append(
                f"{table} has unexpected columns: {', '.join(sorted(unexpected_columns))}"
            )

    description = "; ".join(details) or "schema does not match the expected legacy schema"
    return MigrationBootstrapError(
        "Refusing to migrate an unversioned database because it is not a recognized "
        f"Bourbon Book schema ({description}). Restore from backup or inspect the database "
        "before retrying."
    )


def bootstrap_database(settings: Settings) -> None:
    """Safely bring an empty, legacy, or versioned database to Alembic head."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_database_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        all_tables = set(inspector.get_table_names())
        app_tables = all_tables - {"alembic_version"}
        config = alembic_config(settings.database_url)

        if "alembic_version" in all_tables:
            logger.info("Upgrading versioned database to Alembic head")
            command.upgrade(config, "head")
            return

        if not app_tables:
            logger.info("Initializing empty database at Alembic head")
            command.upgrade(config, "head")
            return

        if app_tables != set(EXPECTED_SCHEMA):
            raise _legacy_schema_error(app_tables, inspector)

        for table, expected_columns in EXPECTED_SCHEMA.items():
            actual_columns = {column["name"] for column in inspector.get_columns(table)}
            if actual_columns != expected_columns:
                raise _legacy_schema_error(app_tables, inspector)

        logger.info("Recognized legacy database; stamping baseline before upgrade")
        command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
    finally:
        engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    bootstrap_database(Settings.from_env())


if __name__ == "__main__":
    main()
