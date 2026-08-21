# Component Design: Catalog Import Pipeline

Modules: `bourbonbook/catalog_uploads.py`, `bourbonbook/catalog_imports.py`,
`bourbonbook/catalog_import_worker.py`, `bourbonbook/catalog_extract.py`, catalog-import routes in
`bourbonbook/main.py`
Governing ADR: [ADR 0002: Local-First Pricing Catalog](../../adr/0002-local-first-pricing-catalog.md)
Related: [HLDD](../hldd.md) · [Pricing & catalog](pricing-and-catalog.md) ·
[Administration & configuration](admin-and-configuration.md) ·
[Persistence & migrations](persistence-and-migrations.md)

## Responsibility

Let an administrator bulk-load MSRP data into the shared `catalog_prices` cache from photographed or
exported price sheets, without ever running a slow, untrusted, model-driven extraction inside an
HTTP request, and without a model's output reaching the shared catalog unreviewed.

Two properties define the design:

- **Extraction never runs in the request path.** The upload route validates and stages; a
  lifespan-owned worker extracts.
- **Nothing is trusted until an administrator applies it.** Extracted rows land in a separate
  staging table as editable *proposals*. A proposal is not a price.

## Durable state machine (`catalog_imports.py`)

`CatalogImportState`: `queued → extracting → review → applied`, with `failed` and `expired` as the
off-ramps. `transition_batch()` is the only sanctioned mutation and rejects any edge not in
`_ALLOWED_TRANSITIONS`:

```
queued     → extracting | failed | expired
extracting → queued (transient retry) | review | failed
review     → applied | expired
failed     → queued (manual retry) | expired
applied    → (terminal)
expired    → (terminal)
```

`transition_batch()` also clears `lease_expires_at` on every target except `extracting`, and stamps
`applied_at` on `applied`, so lease and audit bookkeeping cannot drift from the state.

## Admission control (`reserve_catalog_import_batch()`)

The queue depth check lives **inside** the reservation statement — an
`INSERT … FROM SELECT … WHERE (SELECT count(*) WHERE state='queued') < capacity … RETURNING id`.
Returning `None` means the queue was full. Doing the count as a separate `SELECT` would let two
request sessions both observe the last free slot; embedding the predicate makes over-admission
impossible without a lock. Capacity is `CATALOG_IMPORT_QUEUE_CAPACITY` (default 5).

## Upload validation and staging (`catalog_uploads.py`)

Layered so each check runs before the resource it protects is committed:

1. **Authorize first.** `POST /admin/catalog-import` resolves the admin *before* touching the body,
   and converts the normal `303` login redirect into a flat `403` so an unauthorized caller cannot
   distinguish authorization failure from upload validation behavior.
2. **Bound the body before parsing.** `main.enforce_catalog_import_request_size()` requires a
   `Content-Length` (`411` if absent — a chunked body cannot be bounded here) and rejects anything
   over `CATALOG_IMPORT_MAX_TOTAL_MB` with `413`. This must precede `request.form()`, because
   Starlette's multipart parser spools file parts while it parses.
3. **Per-part limits.** `request.form(max_files=…, max_part_size=…)` from
   `CATALOG_IMPORT_MAX_FILES` and `MAX_UPLOAD_MB`.
4. **Content validation** (`validate_catalog_uploads()`): the declared content type must be
   `image/png`, `image/jpeg`, or `application/pdf` **and** the bytes must start with that type's
   magic signature; per-file size, running aggregate size, and total PDF page count
   (`CATALOG_IMPORT_MAX_PDF_PAGES`) are enforced as it goes.
5. **Decode budgets before allocation.** Images are checked against
   `CATALOG_IMPORT_MAX_IMAGE_PIXELS` / `_MAX_IMAGE_DIMENSION` from the header, with Pillow's
   `DecompressionBombWarning` promoted to an error; PDF pages are checked against
   `_MAX_PDF_RENDER_PIXELS` / `_MAX_PDF_RENDER_DIMENSION` computed from `page.bound()` at
   `PDF_RENDER_SCALE = 2`, i.e. *before* PyMuPDF allocates the pixel buffer. A `0` disables either
   check.
   `create_app()` raises `PIL.Image.MAX_IMAGE_PIXELS` to the larger of the two configured pixel
   ceilings, because Pillow's ~89.5MP default otherwise fires first and silently makes these
   settings inert.
6. **Atomic staging** (`stage_catalog_uploads()`): the whole batch is written into
   `<DATA_DIR>/catalog-imports/.<batch_id>-<uuid>.tmp` at mode `0700` with each file at `0600`, then
   `Path.replace()`d onto `<DATA_DIR>/catalog-imports/<batch_id>/`. The worker therefore never sees
   a partially written batch. Any `OSError` removes both directories and re-raises.

### Source retention

`cleanup_expired_catalog_import_sources()` runs at startup, at worker start, and on every worker
poll iteration. It removes batch directories (and `.tmp` orphans matching
`_TEMP_BATCH_DIRECTORY`) whose mtime is older than `CATALOG_IMPORT_SOURCE_EXPIRY_HOURS`, but the
**durable batch state is the authority**: a `queued` or `extracting` batch keeps its input no matter
how old the directory is, including one requeued after an interrupted lease. A `failed` batch whose
sources have expired is transitioned to `expired`, which is what makes the retry route's "sources no
longer available" error a real, checkable condition rather than a crash.

## The worker (`catalog_import_worker.py`)

One `CatalogImportWorker`, created and `start()`ed by the FastAPI lifespan and `stop()`ed through
the same `AsyncExitStack`. Its `_run()` loop cleans expired sources, calls `process_next()`, and
sleeps `CATALOG_IMPORT_POLL_SECONDS` when there was nothing to do.

- **Claim**: `claim_next_catalog_import()` picks the oldest `queued` id, then takes it with a
  conditional `UPDATE … WHERE id=? AND state='queued'` that also increments `attempt_count` and sets
  `lease_expires_at = now + CATALOG_IMPORT_LEASE_SECONDS`. A zero rowcount means someone else won;
  the worker simply returns.
- **Lease + heartbeat**: while extraction runs, `_heartbeat_until_done()` renews the lease every
  `CATALOG_IMPORT_LEASE_HEARTBEAT_SECONDS`. `recover_expired_catalog_import_leases()` at start
  requeues any `extracting` batch whose lease has already passed. This is the one place in the app
  that cannot use "the process restarted" as its liveness signal, because a legitimate extraction
  can outlive a poll interval by many minutes — hence a real lease rather than the simpler
  startup-sweep approach `bottle_processing.py` uses.
  `Settings.validate_identity()` enforces
  `lease_seconds >= batch_timeout_seconds` and `0 < heartbeat_seconds < lease_seconds`, so a lease
  can never expire under a still-running extraction that is within its own timeout.
- **Single lane**: an `asyncio.Semaphore(1)` guarantees exactly one extraction at a time, so batch
  work never competes with a request-path vision call for the local GPU.
- **Timeouts**: `asyncio.timeout(CATALOG_IMPORT_BATCH_TIMEOUT_SECONDS)` wraps the whole batch;
  `CATALOG_IMPORT_CHUNK_TIMEOUT_SECONDS` bounds each model call inside
  `catalog_extract.extract_catalog_files()`.
- **Failure policy**: `_is_transient()` treats `TimeoutError` and `CatalogExtractionError` with
  `failure_kind in {timeout, transport}` as retryable. A transient failure below
  `MAX_AUTOMATIC_ATTEMPTS = 2` returns the batch to `queued`; anything else is `failed`.
  `error_summary` stores a bounded `failure_kind` (≤40 chars), never exception text.
  `asyncio.CancelledError` is re-raised untouched so a shutdown keeps the lease and sources for
  recovery instead of burning an attempt.
- **Success**: `_persist_proposals()` replaces any prior proposals for the batch, normalizes each
  extracted name/size through `catalog.catalog_price_key()`, stamps
  `price_updated_at = batch.requested_price_updated_at or today`, transitions to `review`, and only
  then deletes the staged sources.
- **Metrics**: `observability.observe_catalog_import(result, queue_wait, duration)` records
  `review`, `retry`, or `failed` with queue-wait and extraction-duration histograms.

## Extraction (`catalog_extract.py`)

Shared by the worker and by `scripts/extract_catalog_screenshots.py` (`make
price-catalog-extract-screenshots`) — two callers, one code path, so a change here affects both.

PDFs are rasterized page by page (`document_chunks()`); tall screenshots are sliced into overlapping
vertical chunks (`image_chunks()`, default 2400px tall, 120px overlap) so a long price list is not
truncated by the model's context window. Each chunk goes to `OLLAMA_VISION_MODEL or OLLAMA_MODEL`
with `OLLAMA_VISION_NUM_CTX` (default `32768`),
prompted to use the sale "Now" price rather than a crossed-out one and to skip incomplete cards.
`parse_catalog_items()` strips code fences and defensively validates each record; `parse_price()`
rejects anything outside `(0, 100000)`; `canonical_size()` normalizes e.g. `750ML`;
`deduplicate_catalog_items()` collapses duplicates introduced by the chunk overlap.

## Review and apply (routes + `apply_catalog_import_batch()`)

| Route | Purpose |
| --- | --- |
| `GET /admin/catalog-import` | Queue overview and the upload form |
| `POST /admin/catalog-import` | Validate, reserve, stage — never extract |
| `GET /admin/catalog-import/{id}` | Paginated proposal review |
| `POST /admin/catalog-import/{id}/review` | Persist edits only; explicitly changes no catalog price |
| `POST /admin/catalog-import/{id}/apply` | Apply included proposals to `catalog_prices` |
| `POST /admin/catalog-import/{id}/delete` | Delete a `queued`/`failed`/`review` batch and its sources |
| `POST /admin/catalog-import/{id}/retry` | Requeue a `failed` batch whose sources still exist |

Every one of these calls `require_admin()` and `verify_csrf()`, and every state-guarded action
re-checks the state in SQL (not just in Python) so a concurrent worker transition cannot be raced:
`delete_catalog_import_batch()` filters on `state IN (queued, failed, review)`,
`retry_failed_catalog_import_batch()` on `state = 'failed'`, and the apply transition on
`state = 'review'` with a `rowcount != 1` check that raises rather than silently proceeding.

Redirects are built by `main.catalog_import_redirect_path()`, which coerces the batch id through
`int()` and the query string through `urlencode()`, so no remote-controlled string can steer the
redirect off-site.

`apply_catalog_import_batch()` runs entirely inside `session.begin()`:

- optional page-scoped `CatalogImportReviewUpdate` edits are applied to their proposals first;
- each included proposal upserts `(product_key, size_key)` into `CatalogPrice` with
  `title="Local screenshot catalog"`, empty `url`, a batch-referencing `basis`, and
  `checked_at = midnight UTC of price_updated_at`;
- an existing row that is **strictly newer**, or same-dated with the same price, is left alone and
  counted as `unchanged` — an import can never move the catalog backwards in time;
- the batch transitions to `applied`; a failure anywhere rolls back every price and leaves the batch
  in `review`.

The returned `CatalogImportApplyResult` carries `created/updated/unchanged/skipped` counts plus the
affected `catalog_price_ids`. `main.sync_applied_catalog_prices_to_qdrant()` then refreshes just
those rows in Qdrant **after** the commit, swallowing and logging any failure — SQLite is the source
of truth and `make price-catalog-reindex` can always repair the index.

## Configuration

| Setting | Default | Effect |
| --- | --- | --- |
| `CATALOG_IMPORT_MAX_FILES` | 5 | Files per upload |
| `CATALOG_IMPORT_MAX_TOTAL_MB` | 50 | Aggregate request size |
| `CATALOG_IMPORT_MAX_PDF_PAGES` | 10 | Total pages across all PDFs |
| `CATALOG_IMPORT_MAX_IMAGE_PIXELS` | 50,000,000 | Decoded image budget; `0` disables |
| `CATALOG_IMPORT_MAX_IMAGE_DIMENSION` | 0 (disabled) | Per-axis image cap |
| `CATALOG_IMPORT_MAX_PDF_RENDER_PIXELS` | 50,000,000 | Rendered PDF page budget; `0` disables |
| `CATALOG_IMPORT_MAX_PDF_RENDER_DIMENSION` | 0 (disabled) | Per-axis rendered-page cap |
| `CATALOG_IMPORT_SOURCE_EXPIRY_HOURS` | 24 | Terminal/orphan source retention |
| `CATALOG_IMPORT_QUEUE_CAPACITY` | 5 | Concurrent `queued` batches |
| `CATALOG_IMPORT_CHUNK_TIMEOUT_SECONDS` | 120 | Per model call |
| `CATALOG_IMPORT_BATCH_TIMEOUT_SECONDS` | 900 | Whole batch |
| `CATALOG_IMPORT_LEASE_SECONDS` | 1200 | Must cover the batch timeout |
| `CATALOG_IMPORT_LEASE_HEARTBEAT_SECONDS` | 60 | Must be inside the lease |
| `CATALOG_IMPORT_POLL_SECONDS` | 1.0 | Idle poll cadence |

Only the first four and `CATALOG_IMPORT_SOURCE_EXPIRY_HOURS` appear in `admin_config.CONFIG_FIELDS`
and `.env.example`; the rest are environment-only today. See [HLDD §7.4](../hldd.md) and §9.

## Design properties worth preserving

- **Extraction outside the request path** is the whole point of the durable queue. A "just run it
  inline for small uploads" shortcut would reintroduce multi-minute request handling and put an
  unbounded model call behind an HTTP timeout.
- **Proposals are not prices.** The `review` state exists so a vision model's misreading is an
  editable row rather than a shared, cross-user MSRP. Auto-applying a batch would remove the only
  human check between an OCR error and every user's estimated collection value.
- **Never move the catalog backwards.** The `unchanged` branch's strict freshness comparison is what
  makes re-importing an old price sheet safe.
- **SQLite commits before Qdrant.** Index updates are post-commit and best-effort by design; making
  the apply depend on Qdrant would turn an optional accelerator into a hard dependency, contradicting
  ADR 0002.
- **State guards belong in SQL.** Every transition is a predicated `UPDATE`/`DELETE` with a rowcount
  check. Checking `batch.state` in Python and then writing is a race against the worker.
