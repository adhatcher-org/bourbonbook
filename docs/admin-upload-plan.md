# Admin Catalog Upload Plan

## Goal

Complete the Admin → Catalog Import workflow so an administrator can upload local PNG, JPEG, or
PDF catalog pages, review extracted bottle-price records, edit or exclude them, and explicitly
apply approved changes to the shared local price catalog.

The existing `/admin/catalog-import` page validates file types only. It does not currently extract
records, show a review, or write catalog data.

## Intended workflow

1. An authenticated administrator opens **Catalog import** from the Administration menu.
2. They upload one or more PNG, JPEG, or PDF files and optionally specify the price-update date.
3. The application saves files beneath `<DATA_DIR>/catalog-imports/` using generated names; never
   trusts the original filename as a path.
4. PNG/JPEG files are cropped into readable image chunks. PDF pages are rendered locally with
   PyMuPDF before they use the same image pipeline.
5. The configured local Ollama vision model extracts only `name`, `size`, and `msrp` from each
   chunk. The price-update date is supplied by the administrator or defaults to the extraction date.
6. Results are normalized, validated, deduplicated by `(normalized name, size, price)`, and stored
   as a persisted import batch. No catalog price is changed at this stage.
7. The administrator is redirected to a review page containing all proposed rows.
8. The review page shows whether each row is **new**, **updates an existing stale/missing price**,
   or **leaves a fresh catalog price unchanged**.
9. The administrator can edit name, size, MSRP, and update date; deselect rows; or delete rows from
   the proposed batch.
10. **Apply selected changes** atomically upserts approved `(name, size)` records into
    `CatalogPrice`, sets `checked_at` to the approved price-update date, and updates Qdrant.
11. The app displays counts for added, updated, unchanged, skipped, and invalid rows. It retains the
    batch summary for audit/debugging and safely removes temporary source files on expiry.

## Persistence and migration

Add an additive Alembic migration for an import-batch table, for example `catalog_import_batches`:

| Field | Purpose |
| --- | --- |
| `id` | Generated batch identifier |
| `created_by_user_id` | Admin who uploaded it |
| `state` | `extracting`, `review`, `applied`, `failed`, or `expired` |
| `records_json` | Validated proposal rows and row-level decisions |
| `source_file_count` | Operational summary only |
| `created_at`, `updated_at`, `applied_at` | Lifecycle/audit times |
| `error_summary` | Bounded, non-sensitive failure information |

Do not store original external session credentials, browser cookies, prompts, raw model responses,
or OHLQ URLs. Source files are local administrator uploads and should be deleted on expiry.

## Routes and authorization

All routes must require `require_admin`, verified sessions, and CSRF protection.

| Route | Behavior |
| --- | --- |
| `GET /admin/catalog-import` | Upload form and recent batch summaries |
| `POST /admin/catalog-import` | Validate/upload files and create an extraction batch |
| `GET /admin/catalog-import/{batch_id}` | Editable review table |
| `POST /admin/catalog-import/{batch_id}` | Save edits, exclusions, or delete proposed rows |
| `POST /admin/catalog-import/{batch_id}/apply` | Apply selected rows atomically |
| `POST /admin/catalog-import/{batch_id}/delete` | Delete an unapplied batch and source files |

The current upload endpoint must be changed from its placeholder success message to creating a real
batch and redirecting to its review page.

## Extraction contract

Reuse the local extraction helpers in `bourbonbook.catalog_extract` and
`scripts.extract_catalog_screenshots.py`, but move reusable document-rendering and Ollama request
logic into a package module so web routes do not import a CLI script.

Required validation:

- allow only PNG, JPEG, and PDF content types/extensions;
- impose existing upload-size limits per file and a bounded total batch size/page count;
- reject unreadable images/PDFs with row-safe error messages;
- require non-empty name, recognized bottle size, and positive price;
- use the displayed current sale price for `Now $x was $y` cards;
- use the supplied `price_updated_at` date, never invent a source-change date;
- deduplicate crop overlap without merging different package sizes;
- make no outbound browser, OHLQ, OpenAI, or credential/session call.

## Review-page requirements

The table should include: include checkbox, proposed action, bottle name, size, MSRP, update date,
and validation warning. It must support bulk include/exclude, inline edits, select all visible rows,
pagination for large batches, and a clear destructive confirmation before apply/delete.

The page must clearly state that extracted values are proposals and that **Apply selected changes**
is the only operation that changes the shared catalog.

## Catalog application rules

- Exact normalized `(name, size)` is the authoritative upsert key.
- New pairs create `CatalogPrice` rows.
- Approved rows update MSRP and `checked_at`.
- Re-index every created/updated row in Qdrant after the SQL transaction succeeds; SQLite remains
  authoritative if Qdrant is unavailable.
- Preserve the existing six-month user-purchase-price behavior: a user purchase price fills or
  replaces only missing/stale catalog data.
- Do not create user `Bottle` collection entries from an import; this feature updates the shared
  price catalog only.

## Logging and metrics

Log batch lifecycle events with batch ID, admin user ID, file/page/chunk counts, model, durations,
and outcome counts. Do not log extracted bottle names, uploaded image contents, raw prompts, raw
responses, URLs, or secrets. Add Prometheus counters/histograms for started/completed/failed batches,
records proposed/applied/skipped, and local vision duration.

## Tests and validation

Add deterministic tests with fake Ollama clients and generated small image/PDF fixtures for:

1. admin-only access and CSRF enforcement;
2. valid PNG/JPEG/PDF upload and invalid/oversized file rejection;
3. PDF multi-page rendering;
4. model JSON parsing, malformed output, duplicate crops, and size-specific records;
5. review edits, exclusions, delete action, and apply confirmation;
6. new catalog row creation, existing-row update, unchanged-row behavior, and rollback on failure;
7. Qdrant unavailable behavior while SQLite remains correct;
8. temporary file and expired-batch cleanup;
9. desktop and mobile visual checks of upload, empty, error, review, and applied states.

Run `make test`, `make coverage`, lint/format checks, and `make build`. Test the first live import
with `AmericanWhiskey1.png` only; leave `AmericanWhiskey2.png` untouched for the administrator's
manual test.
