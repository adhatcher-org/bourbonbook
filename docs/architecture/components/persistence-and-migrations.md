# Component Design: Persistence & Migrations

Modules: `bourbonbook/database.py`, `bourbonbook/models.py`, `bourbonbook/migrations.py`,
`migrations/versions/0001`-`0011`
Related: [HLDD](../hldd.md) · [C3 Components](../c3-components.md)

## Responsibility

Own the SQLAlchemy engine/session lifecycle, define the ORM schema, and safely bring any database
state (fresh, pre-Alembic legacy, or already-versioned) up to the current migration head at process
startup.

## Engine and session (`database.py`)

- `create_database_engine(database_url)` builds the SQLAlchemy engine. For SQLite URLs it passes
  `connect_args={"check_same_thread": False}` and registers an `event.listens_for(engine,
  "connect")` hook that runs `PRAGMA foreign_keys=ON` on every new connection — SQLite does not
  enforce FK constraints by default, so this is required for referential integrity to actually hold.
- `Database` wraps the engine plus `sessionmaker(engine, expire_on_commit=False)`.
  `expire_on_commit=False` avoids re-fetching ORM attributes immediately after a commit, which
  matters because code paths like `authenticate_session()` read `user.session_version` right after
  a commit elsewhere in the same request.
- `Database.session()` yields one `Session` per request/call inside a `with` block, closing it
  afterward — the standard scoped-session-per-request pattern.
- `create_all()` is a `Base.metadata.create_all()` convenience, mainly for tests; production schema
  changes always go through Alembic.

## Schema (`models.py`)

Ten tables:

| Table | Key columns | Notes |
| --- | --- | --- |
| `users` | `username`, `email` (unique, nullable during migration windows), `screen_name`, `avatar_name`, `email_verified_at`, `is_admin`, `session_version`, `collection_share_token_hash` (unique), `password_hash` | Central identity row; `bottles`, `tokens`, and `catalog_import_batches` relationships cascade-delete |
| `user_tokens` | `user_id` FK (`CASCADE`), `purpose`, `token_hash` (unique), `email_snapshot`, `expires_at`, `used_at` | One-time verify/reset tokens; see [Identity & sessions](identity-and-sessions.md) |
| `bottles` | `owner_id` FK, ~25 collection-tracking columns, `on_shopping_list`, `status`, `fill_level`, `analysis_status`, `processing_stage` (indexed), `processing_error` | `UniqueConstraint(owner_id, photo_name)`; `estimated_value` is a computed property (`msrp or purchase_price, times quantity`), not a stored column |
| `price_sources` | `bottle_id` FK, `kind`, `title`, `url`, `basis`, `checked_at` | Per-bottle price evidence; ordered by `kind` on load |
| `catalog_prices` | `product_key` + `size_key` (unique together), `msrp`, `title`, `url`, `basis`, `checked_at` | Shared, cross-user MSRP cache — see [Pricing & catalog](pricing-and-catalog.md) and ADR 0002 |
| `product_attribution_facts` | `product_key` + `field` (unique together), `value`, `outcome`, `title`, `url`, `basis`, `checked_at` | Shared, cross-user grounded producer/mash-bill cache with a 365-day TTL (`product_attributions.TTL`) — see [AI analysis](ai-analysis.md) and ADR 0004 |
| `bottle_attribution_provenance` | `bottle_id` FK (`CASCADE`) + `field` (unique together), `authority`, `observed_at`, `fact_id` FK (`SET NULL`) | Per-bottle record of where each attribution came from; `authority` is what protects a hand-verified value from being overwritten |
| `catalog_import_batches` | `created_by_user_id` FK, `state` (indexed), `requested_price_updated_at`, `source_file_count`, `attempt_count`, `lease_expires_at` (indexed), `error_summary`, `applied_at` | Durable import job state; see [Catalog import pipeline](catalog-import.md) |
| `catalog_import_proposals` | `batch_id` FK (`CASCADE`), `position` (unique with `batch_id`), `included`, `name`, `product_key` (indexed), `size_key`, `msrp`, `price_updated_at`, `validation_error` | Editable extracted rows; **never** a user bottle and never a price until applied |
| `api_usage` | `provider`, `operation`, `model`, `success`, `error_type`, `duration_ms`, token-count columns, `user_id` FK (`SET NULL`) | AI/API usage ledger; deliberately excludes prompts/responses/PII |

`Bottle.estimated_value` is the only computed (non-persisted) property on any model.

Several columns carry enum-like values that are defined in Python but stored as plain `String`, so
the database will not reject a bad value: `Bottle.processing_stage`
(`bottle_processing.BottleProcessingStage`), `CatalogImportBatch.state`
(`catalog_imports.CatalogImportState`), and the attribution tables' `field`/`outcome`/`authority`
columns (`product_attributions`). The integrity guarantee for the latter comes from
`transition_batch()` and the predicated `UPDATE`s in the routes and worker, not from a constraint.

## Schema evolution

| Migration | Adds |
| --- | --- |
| `0001_current_schema` | Baseline: `users` (username/display_name/password_hash only — no email), `bottles`, `price_sources` |
| `0002_user_email_identity` | `users.email/screen_name/email_verified_at/is_admin/session_version` (with backfill + collision check); new `user_tokens` table |
| `0003_api_usage` | New `api_usage` table |
| `0004_shopping_list` | `bottles.on_shopping_list` (+ index) |
| `0005_collection_sharing` | `users.collection_share_token_hash` (+ unique index), `users.collection_shared_at` |
| `0006_user_avatars` | `users.avatar_name` |
| `0007_catalog_prices` | New `catalog_prices` table, backfilled from existing OHLQ-sourced `price_sources` rows |
| `0008_catalog_import_persistence` | New `catalog_import_batches` and `catalog_import_proposals` tables (+ indexes) |
| `0009_bottle_processing_stage` | `bottles.processing_stage` (`NOT NULL`, `server_default="idle"`, indexed) and `bottles.processing_error` |
| `0010_bottle_lifecycle_dates` | `bottles.date_bottled` and `bottles.date_purchased` (both nullable `Date`) |
| `0011_product_attributions` | New `product_attribution_facts` and `bottle_attribution_provenance` tables (+ indexes) |

`HEAD_REVISION` in `migrations.py` is kept in sync with the latest file
(`"0011_product_attributions"`) and is what `/readyz` compares against. `0009` is still the pattern
to copy for a new non-nullable column: a `server_default` so existing rows migrate without a
backfill pass, added via `op.add_column` on SQLite.

`EXPECTED_SCHEMA` in `migrations.py` deliberately does **not** track these later tables — it
describes the *pre-Alembic legacy* shape (`users`, `bottles`, `price_sources` as they existed at
`0001`) used to decide whether an unversioned database is safe to stamp. It is not a description of
the current schema and should not be updated when a new table is added.

## Bootstrap (`bootstrap_database()`)

Runs once from `entrypoint.py` before Uvicorn starts, and again (idempotently) inside
`main.create_app()`'s lifespan. It safely classifies the database into one of three states:

```mermaid
flowchart TD
  start([bootstrap_database]) --> has_alembic{alembic_version\ntable present?}
  has_alembic -- yes --> upgrade[command.upgrade to head]
  has_alembic -- no --> has_tables{Any app tables\npresent?}
  has_tables -- no --> fresh[Fresh DB:\nupgrade to head\ncreates everything from 0001]
  has_tables -- yes --> matches{Table/column set matches\nEXPECTED_SCHEMA exactly?\n users, bottles, price_sources}
  matches -- yes --> stamp[Stamp at BASELINE_REVISION\n= 0001_current_schema,\nthen upgrade to head]
  matches -- no --> refuse[Raise MigrationBootstrapError\nwith a detailed diff.\nRefuses to guess.]
```

The "refuse to guess" branch is a deliberate safety valve: an unrecognized legacy schema fails
startup loudly with a diff, rather than silently mutating data the migration author never saw. A CLI
entrypoint (`python -m bourbonbook.migrations`) exposes the same logic for manual invocation.

## Design properties worth preserving

- Every migration is forward-only and has been exercised against both a fresh database and an
  upgraded copy-shaped legacy database (per `docs/adr/plan.md`'s cross-cutting testing requirement).
- `CatalogPrice` is intentionally not scoped to a user — any schema change that adds ownership to it
  would change the sharing semantics described in ADR 0002.
- SQLite's `check_same_thread=False` plus a single Uvicorn worker means the app never needs a
  connection pool sized for concurrent writers; this is a direct consequence of ADR 0001's
  single-process deployment decision and should not be "fixed" without revisiting that ADR.
- The background paths (`bottle_processing`, `CatalogImportWorker`) each open their own short-lived
  sessions from `session_factory()` rather than sharing a request session or holding one open across
  a model call. Holding a session across a multi-minute extraction would pin a SQLite connection for
  the duration on a single-writer database.
- `catalog_import_proposals` is staging, not collection data. Any change that lets a proposal be
  read or written outside an admin route, or that skips the `review` state on the way to
  `catalog_prices`, changes the trust model described in ADR 0002.
