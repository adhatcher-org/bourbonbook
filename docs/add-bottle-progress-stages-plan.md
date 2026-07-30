# Add-Bottle Real-Time Progress Stages Plan

## Context

`POST /bottles` (`bourbonbook/main.py:2492-2543`, `add_bottle`) runs three slow, sequential
network calls synchronously inside one request: `analyze_bottle()` (Ollama vision call, 20-30s+),
`enrich_bottle_by_name()` (local catalog/Qdrant lookup), and `apply_user_purchase_price()` /
`refresh_prices()` (local price cache or live provider search). The frontend
(`bourbonbook/templates/new.html`, `bourbonbook/static/app.js:31-37`) is a plain
`<form method="post">`; on submit it shows a static spinner and blocks on the full 25-45s
request/response cycle before the server's 303 redirects to the edit page. There is no way to
show which stage is running, and the product owner has explicitly rejected a client-side fake
timer — the UI must reflect real backend stage transitions.

This repo already solves an analogous problem for catalog-price imports:
`bourbonbook/catalog_import_worker.py`'s `CatalogImportWorker` is a lifespan-owned, single-lane
background worker with a persisted state machine (`bourbonbook/catalog_imports.py`,
`CatalogImportState`), claim-by-conditional-`UPDATE`, and lease/heartbeat crash recovery. That
worker exists specifically because "extraction never executes in the HTTP request path." This plan
follows that established principle but does **not** copy the full machinery: catalog import needs
leases/heartbeats/automatic retry because a batch can run for up to 15 minutes across multiple
files and multiple worker "ticks." Bottle processing is a single ~25-45s job that starts the
instant its one HTTP request finishes, its three stage functions (`analyze_bottle`,
`enrich_bottle_by_name`, `refresh_prices`) already never raise — they catch their own
provider/network errors and return `(values, "unavailable")` (see `bourbonbook/ollama.py:189` and
`bourbonbook/analysis.py:249-270`) — and this app runs as a single `uvicorn` process with no
`--workers` flag (`bourbonbook/entrypoint.py`). Given that, FastAPI's existing `BackgroundTasks`
(already used for `warm_analysis_model` at `bourbonbook/main.py:2489`) plus a persisted stage
column and a trivial startup-orphan sweep is the smallest mechanism that is still genuinely
crash-safe. No Celery/Redis/message queue, no lease/heartbeat loop, no SSE/WebSocket server.

Transport: this codebase has **no** existing SSE, WebSocket, or client-side polling precedent
anywhere (`grep`ed `bourbonbook/main.py`, `templates/*.html`, `static/*.js` — the only prior
"live" admin UI, `admin/catalog_import.html`, requires a manual page reload to see state change).
Given `MAX_USERS=10` (`.env.example`), a single small FastAPI app, SQLite in WAL mode with a
5000ms busy timeout (`bourbonbook/database.py`, commit `4b1186c`), and only three discrete stage
transitions to observe (not a token stream), plain polling is recommended over SSE/WebSocket:
- No new dependency, no long-lived connection, no reverse-proxy buffering config
  (`proxy_headers`/`FORWARDED_ALLOW_IPS` in `bourbonbook/config.py` say nothing about SSE, and an
  Unraid/nginx front end would need explicit `proxy_buffering off` to make SSE work reliably —
  an operational risk this app has never had to manage).
- A `GET` polling endpoint is trivially testable with the existing `TestClient` conventions used
  throughout `tests/test_app.py` (no `StreamingResponse` consumer needed in tests).
- At 10 max users, a ~1s poll cadence against one indexed SQLite row read is negligible load.

## Data model

Extend `Bottle` (`bourbonbook/models.py:67-114`) with two new columns — **not** by overloading
`analysis_status`. `analysis_status` (`"manual"`/`"unavailable"`/`"complete"`/`"verified"`) is a
**terminal outcome** value already read by `edit.html:10` to choose the analysis-result banner
and copy ("Verified bottle details applied" / "Bottle lookup unavailable" / etc.). Reusing it for
transient "analyzing…" states would mean a bottle mid-pipeline — now visible immediately in the
main collection query and on direct navigation to its detail/edit URL, since the row exists before
processing finishes — could render `edit.html`'s existing banner logic with a value it was never
written to handle. Keeping them separate means zero changes are needed to that existing
partial-success banner logic.

```python
# bourbonbook/models.py — new Bottle columns
processing_stage: Mapped[str] = mapped_column(String(20), default="idle", index=True)
processing_error: Mapped[str | None] = mapped_column(Text)
```

New module `bourbonbook/bottle_processing.py` defines the vocabulary (mirrors
`CatalogImportState`'s `StrEnum` style in `catalog_imports.py:16-23`):

```python
class BottleProcessingStage(StrEnum):
    IDLE = "idle"            # default; bottle never went through the async pipeline
    QUEUED = "queued"        # row created, BackgroundTasks callback scheduled
    ANALYZING = "analyzing"  # stage 1: analyze_bottle() (vision call)
    ENRICHING = "enriching"  # stage 2: enrich_bottle_by_name()
    PRICING = "pricing"      # stage 3: apply_user_purchase_price() / refresh_prices()
    COMPLETE = "complete"    # pipeline finished (outcome quality is in analysis_status, not here)
    FAILED = "failed"        # unexpected exception, or orphaned by a server restart

IN_PROGRESS_STAGES = (
    BottleProcessingStage.QUEUED,
    BottleProcessingStage.ANALYZING,
    BottleProcessingStage.ENRICHING,
    BottleProcessingStage.PRICING,
)
```

`processing_error` is never rendered to end users — it exists only so a stuck/failed row is
debuggable from the database, matching `CatalogImportBatch.error_summary`'s role but without a UI
surface (no admin review screen is being added for this).

### Migration

New `migrations/versions/0009_bottle_processing_stage.py`, modeled directly on
`0004_shopping_list.py`'s `add_column` + `create_index` pattern (SQLite accepts `ADD COLUMN`
without Alembic's batch mode here, same as every prior migration in this repo):

```python
revision = "0009_bottle_processing_stage"
down_revision = "0008_catalog_import_persistence"

def upgrade() -> None:
    op.add_column(
        "bottles",
        sa.Column("processing_stage", sa.String(length=20), nullable=False, server_default="idle"),
    )
    op.add_column("bottles", sa.Column("processing_error", sa.Text(), nullable=True))
    op.create_index("ix_bottles_processing_stage", "bottles", ["processing_stage"])

def downgrade() -> None:
    op.drop_index("ix_bottles_processing_stage", table_name="bottles")
    op.drop_column("bottles", "processing_error")
    op.drop_column("bottles", "processing_stage")
```

Also bump `HEAD_REVISION = "0009_bottle_processing_stage"` in `bourbonbook/migrations.py:16` —
easy to miss; `tests/test_migrations.py` asserts a fresh database reaches this constant.

## Backend architecture change

### `bourbonbook/bottle_processing.py` (new module)

```python
async def run_add_bottle_pipeline(
    session_factory: Callable[[], Session],
    bottle_id: int,
    settings: Settings,
    price_index: QdrantPriceIndex | None,
    usage_recorder: AIUsageRecorder | None,
    user_id: int,
) -> None:
    """Runs stages 1-3 for one bottle, committing after each so pollers see live progress.

    Never raises: any unexpected exception is caught, persisted as processing_stage="failed" +
    processing_error, and logged. FastAPI's BackgroundTasks execution is part of the ASGI
    response cycle (Starlette runs it via Response.__call__ after the body is sent); an
    uncaught exception here would surface as a server error against a request that has already
    returned 202 to the client, and would strand the bottle at whatever stage it last committed.
    """
```

Internally this is today's `add_bottle` body (`bourbonbook/main.py:2506-2540`), split into three
commit points instead of one, each opening **its own session** via `session_factory()` — never the
request's session, which is already closed (`with app.state.database.session_factory() as
session:` exits before `BackgroundTasks` run). This directly matches the guidance already spelled
out in `catalog_import_worker.py`'s methods (`_persist_proposals`, `_record_failure`), which each
open a fresh `with self._session_factory() as session:` block rather than holding one open across
awaits.

Sequence, each step ending in `session.commit()` immediately after the stage's DB write so a
concurrent poller's read (a separate connection; safe under WAL) observes the new stage without
waiting on this transaction:

1. Set `bottle.processing_stage = ANALYZING`, commit.
2. `analysis, analysis_status = await analyze_bottle(photo_path, settings)` inside
   `usage_context(usage_recorder, user_id)` (unchanged from today). Apply via `apply_analysis`.
3. Set `bottle.processing_stage = ENRICHING`, commit.
4. If `bottle.name` is meaningful: `enrich_bottle_by_name(...)`, apply, unchanged logic.
5. Set `bottle.processing_stage = PRICING`, commit.
6. `apply_user_purchase_price(...)` then `refresh_prices(...)` if needed, unchanged logic.
7. Set `bottle.processing_stage = COMPLETE`, commit.

Wrap steps 1-7 in `try/except Exception` (`BaseException` minus cancellation is enough — these
stage functions are `await`ed, not raw threads); on exception, open a fresh session, set
`processing_stage = FAILED`, `processing_error = repr(exc)[:2000]`, commit, and `log_event(...,
logging.ERROR, "bottle_processing_failed", ...)`. This is a genuine new failure path (today a
raised exception mid-`add_bottle` would 500 the request and roll back everything, including
`session.add(bottle)`, which hadn't happened yet); under the new design the bottle row already
exists and commits per-stage, so a crash after stage 1 leaves a real, partially-analyzed row
behind — which is *more* recoverable than today, not less, since the user can still open it from
"Failed" state and use the existing `/bottles/{id}/analyze` retry endpoint.

```python
def recover_orphaned_bottle_processing(session: Session) -> int:
    """Startup-only sweep: any row still IN_PROGRESS_STAGES at process start was abandoned by
    the previous process (single-worker deployment => no distributed race, no lease/TTL needed
    to disambiguate 'still running' from 'orphaned' the way CatalogImportWorker's lease does)."""
    result = session.execute(
        update(Bottle)
        .where(Bottle.processing_stage.in_([s.value for s in IN_PROGRESS_STAGES]))
        .values(processing_stage=BottleProcessingStage.FAILED.value,
                processing_error="Interrupted by server restart")
    )
    return int(result.rowcount or 0)
```

### `bourbonbook/main.py` wiring

- **Lifespan** (`create_app`, around line 191): after `bootstrap_database(settings)`, call
  `with database.session_factory() as session: recovered = recover_orphaned_bottle_processing(session); session.commit()`
  and `log_event(..., logging.WARNING, "bottle_processing_recovered", ..., recovered=recovered)`
  if `recovered`, mirroring the existing `recover_expired_catalog_import_leases` startup call in
  `CatalogImportWorker.start()`. No new worker object, no `.start()`/`.stop()` lifecycle needed —
  this is a one-shot sweep, not a running loop.

- **`add_bottle`** (`bourbonbook/main.py:2492-2543`) — keep photo save + validation synchronous
  (unchanged: `save_photo` can still raise `HTTPException` before any row exists, exactly as
  today), but stop calling `analyze_bottle`/`enrich_bottle_by_name`/`refresh_prices` inline. Add a
  `background_tasks: BackgroundTasks` parameter (already imported; same pattern as `new_bottle` at
  line 2486-2490):

  ```python
  @app.post("/bottles", status_code=202)
  async def add_bottle(
      request: Request,
      background_tasks: BackgroundTasks,
      photo: Annotated[UploadFile, File()],
      purchase_price: Annotated[str, Form()] = "",
      quantity: Annotated[str, Form()] = "1",
      csrf: Annotated[str, Form(alias="csrf_token")] = "",
  ) -> Response:
      verify_csrf(request, csrf)
      with app.state.database.session_factory() as session:
          user = require_verified_user(request, session)
          photo_name = await save_photo(photo, app.state.settings.data_dir / "uploads",
                                         app.state.settings.max_upload_mb)
          bottle = Bottle(
              owner_id=user.id, photo_name=photo_name,
              purchase_price=parse_float(purchase_price),
              quantity=parse_int(quantity, 1, 1, 99),
              processing_stage=BottleProcessingStage.QUEUED.value,
          )
          session.add(bottle)
          session.commit()
          bottle_id = bottle.id
      background_tasks.add_task(
          run_add_bottle_pipeline, app.state.database.session_factory, bottle_id,
          app.state.settings, app.state.qdrant_price_index, app.state.usage_recorder, user.id,
      )
      return JSONResponse({"bottle_id": bottle_id}, status_code=202)
  ```

  This response contract change (303 redirect → 202 JSON) is why the frontend must move to
  `fetch()` — a plain `<form method="post">` cannot handle a JSON response, and blocking on it
  would defeat the entire point. There is no non-JS fallback: `new.html` already depends on JS for
  the photo-preview canvas and this app already registers a service worker unconditionally
  (`app.js:197-199`), so requiring JS here is consistent with the existing baseline.

- **New endpoint** `GET /bottles/{bottle_id}/status`:

  ```python
  @app.get("/bottles/{bottle_id}/status")
  def bottle_processing_status(request: Request, bottle_id: int) -> Response:
      with app.state.database.session_factory() as session:
          user = require_verified_user(request, session)
          bottle = owned_bottle(session, user, bottle_id)
          if not bottle:
              return JSONResponse({"error": "not_found"}, status_code=404)
          done = bottle.processing_stage in {s.value for s in
                                              (BottleProcessingStage.COMPLETE, BottleProcessingStage.FAILED)}
          return JSONResponse({
              "stage": bottle.processing_stage,
              "analysis_status": bottle.analysis_status,
              "done": done,
          })
  ```

  Auth: reuses `require_verified_user` + `owned_bottle` exactly like every other bottle route —
  same session-cookie auth as the rest of the app, no new auth mechanism. No CSRF check: it's a
  side-effect-free `GET`, consistent with every other `GET` route in `main.py`.

- **`collection_statement`** (`bourbonbook/main.py:720-741`): a bottle now exists in the database
  (with placeholder `name="Untitled bottle"` etc.) the instant the request returns, before
  analysis finishes — previously it didn't exist until the whole pipeline succeeded. Anyone who
  navigates to `/` in another tab during those ~30s would see an incomplete placeholder card today
  without this change. Add one more filter clause, following the exact style of the existing
  `Bottle.status != "Empty"` / `Bottle.on_shopping_list.is_(False)` exclusions:

  ```python
  statement = select(Bottle).where(
      Bottle.owner_id == user.id,
      Bottle.status != "Empty",
      Bottle.on_shopping_list.is_(False),
      Bottle.processing_stage.notin_([s.value for s in IN_PROGRESS_STAGES]),
  )
  ```

  Direct navigation to `/bottles/{id}` or `/bottles/{id}/edit` for a still-processing bottle is
  left as-is (renders whatever is currently in the row — same tolerant behavior the edit page
  already has for any partially-filled bottle); no special-casing needed there.

## Frontend changes

`bourbonbook/templates/new.html`: give the overlay a stage-text hook and an error slot:

```html
<div class="analyzing" data-analyzing hidden role="status" aria-live="polite">
  <span class="spinner"></span>
  <strong data-analyzing-text>Analyzing bottle…</strong>
  <small>This can take a minute on the first run.</small>
</div>
<div class="form-alert" data-add-bottle-error hidden role="alert"></div>
```

`bourbonbook/static/app.js` replaces the current submit handler (lines 31-37):

```js
const uploadForm = document.querySelector('[data-upload-form]');
if (uploadForm) {
  const STAGE_TEXT = {
    queued: 'Starting…',
    analyzing: 'Analyzing bottle…',
    enriching: 'Getting bottle details…',
    pricing: 'Checking pricing…',
  };
  const POLL_MS = 1200;
  const POLL_TIMEOUT_MS = 120_000;

  uploadForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const errorBox = document.querySelector('[data-add-bottle-error]');
    errorBox.hidden = true;
    document.querySelector('[data-analyzing]').hidden = false;
    document.querySelector('[data-submit-button]').disabled = true;
    try {
      const response = await fetch('/bottles', {
        method: 'POST',
        body: new FormData(uploadForm),
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('add_bottle_failed');
      const { bottle_id: bottleId } = await response.json();
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      while (Date.now() < deadline) {
        const statusResponse = await fetch(`/bottles/${bottleId}/status`, { credentials: 'same-origin' });
        if (!statusResponse.ok) throw new Error('status_failed');
        const status = await statusResponse.json();
        const text = document.querySelector('[data-analyzing-text]');
        if (text && STAGE_TEXT[status.stage]) text.textContent = STAGE_TEXT[status.stage];
        if (status.done) {
          window.location.href = `/bottles/${bottleId}/edit?new=1`;
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }
      throw new Error('timed_out');
    } catch {
      document.querySelector('[data-analyzing]').hidden = true;
      document.querySelector('[data-submit-button]').disabled = false;
      errorBox.textContent = 'Something went wrong saving this bottle. Please try again.';
      errorBox.hidden = false;
    }
  });
}
```

Notes: `credentials: 'same-origin'` is the browser default for same-origin `fetch`, kept explicit
for clarity. The CSRF token still travels as a normal `FormData` field (`csrf_token` hidden input,
unchanged), so `verify_csrf` needs no change. A 120s client-side poll timeout guards against a
genuinely hung Ollama call (no server-side timeout currently wraps `analyze_bottle`, so the
pipeline itself could in theory run long — this is an existing gap, not introduced by this
change) by surfacing an error instead of polling forever; the bottle row still exists and is
recoverable via the existing edit page even if the client gives up.

## Error handling — preserving today's partial-success behavior

Nothing about *what* `analyze_bottle`/`enrich_bottle_by_name`/`refresh_prices` return changes, and
`normalized_analysis_status`/`analysis_redirect_query`/`edit.html`'s banner logic are untouched.
The only new failure mode is the pipeline-level `try/except` added above, for cases those
functions don't already cover (a DB write failing, a bug). Concretely:

- Stage 1 fails/unavailable → `analysis_status` ends up `"unavailable"`, bottle still gets
  `processing_stage = COMPLETE` (the *pipeline* succeeded even though the *analysis* didn't) —
  identical outcome to today's synchronous path, just reached asynchronously.
- An actual exception (not a graceful "unavailable") → `processing_stage = FAILED`. The frontend's
  poll loop treats `done: true` for `FAILED` the same as `COMPLETE` — it still redirects to
  `/bottles/{id}/edit?new=1`. The bottle row exists with whatever partial data was committed before
  the failure (at minimum: photo, price, quantity from the initial insert), so the user lands on a
  normal, editable page rather than a dead end. This is a strict improvement over today, where an
  uncaught exception mid-`add_bottle` would 500 and lose the upload entirely.

## Testing strategy

Follow `tests/test_catalog_import_worker.py`'s pattern of calling pipeline functions directly and
deterministically rather than sleeping/polling in tests — but note an even simpler property here:
Starlette's `TestClient` (`fastapi.testclient.TestClient`, ASGI transport) executes
`BackgroundTasks` synchronously as part of finishing the request before `client.post(...)` returns
control to the test. This is already implicitly proven by the existing
`test_new_bottle_page_schedules_a_vision_model_warm_up` (`tests/test_app.py:548-560`), which
asserts a background-scheduled call happened immediately after `client.get(...)` returns. So:

- **`tests/test_app.py`**: update `test_add_review_edit_and_view_bottle` (currently asserts
  `response.status_code == 303` at line 599) to instead assert `response.status_code == 202` and
  `response.json()["bottle_id"]`, then immediately `GET /bottles/{id}/status` and assert
  `{"stage": "complete", ...}` (the background task already ran), then continue exactly as today
  via `client.get(f"/bottles/{bottle_id}/edit?new=1")`. Add a case posting with `analyze_bottle`
  monkeypatched to raise, asserting `processing_stage == "failed"` and that `/status` still
  returns `done: true` with a usable bottle row. Add a case checking `/` (collection page) does
  *not* list a bottle mid-`queued`/`analyzing` stage (monkeypatch `analyze_bottle` to an
  `asyncio.Event`-gated coroutine to freeze it mid-flight, or directly seed a row with
  `processing_stage="analyzing"` and assert it's excluded from `collection_statement`'s results).
- **`tests/test_bottle_processing.py`** (new): unit-test `run_add_bottle_pipeline` directly
  (`asyncio.run(...)`, monkeypatching `bourbonbook.bottle_processing.analyze_bottle` etc. the same
  way `tests/test_analysis.py` fakes providers) asserting the stage sequence commits
  (`analyzing`→`enriching`→`pricing`→`complete`) by re-reading the row between awaits via a second
  session, an exception-mid-pipeline case landing on `failed` with `processing_error` set, and
  `recover_orphaned_bottle_processing` flipping seeded `analyzing`/`pricing` rows to `failed` and
  leaving `complete`/`idle` rows untouched.
- **`tests/test_migrations.py`**: extend the existing upgrade/downgrade round-trip assertions
  (pattern already there for `on_shopping_list` etc.) to cover `processing_stage` defaulting to
  `"idle"` on a pre-existing row and the new index existing; bump any hardcoded expectations
  keyed on `HEAD_REVISION`.
- Run everything with `pytest -xvs` per this project's standing convention.

## Rollout risk / scope

- **BackgroundTasks don't survive a process restart mid-pipeline.** Accepted: this app deploys as
  one `uvicorn` process (`bourbonbook/entrypoint.py`, no `--workers`), restarted only on deploy or
  crash (`compose.yaml`: `restart: unless-stopped`). The startup `recover_orphaned_bottle_processing`
  sweep bounds the damage to "stuck bottles get marked failed and are immediately usable/editable
  on next boot" rather than "silently spin forever" — no lease/TTL bookkeeping needed because a
  single-process restart means anything mid-flight is unconditionally orphaned, unlike
  `CatalogImportWorker`'s multi-tick lease design which has to tell "still running" apart from
  "abandoned" without a restart as the signal.
- **No cross-request concurrency change.** `add_bottle` was already `async def`, so two users
  submitting bottles concurrently today already run two concurrent Ollama calls; this plan doesn't
  add or remove that. A single-lane semaphore (like `CatalogImportWorker`'s) to serialize Ollama
  calls and avoid vision/text model-swap thrashing on the host would be a reasonable follow-up but
  is out of scope — it isn't a regression this change introduces, and `MAX_USERS=10` makes
  simultaneous submissions rare.
- **New always-visible failure surface**: a genuinely unexpected exception now leaves a visible,
  half-filled bottle row (previously it just 500'd and nothing was created). This is intentional
  and strictly more recoverable, but is a user-visible behavior change worth calling out in the PR
  description — first-run testing should include forcing a stage-3 exception to confirm the edit
  page renders sanely.
- **Explicitly not doing**: SSE/WebSocket transport, Celery/Redis/any external queue, a lease or
  heartbeat mechanism, an admin UI for stuck bottles, retry-with-backoff for transient failures
  (unnecessary — the three stage functions already degrade to `"unavailable"` instead of raising).
  `bourbonbook/main.py:2620-2695`'s existing `/bottles/{id}/analyze` re-analysis endpoint remains
  fully synchronous and out of scope for this change; it has the same 20-30s blocking profile
  today and would be a natural follow-up for the same treatment, but the product ask here was
  specifically the add-bottle flow.

## Implementation sequence

1. `bourbonbook/models.py` — add `processing_stage`/`processing_error` columns to `Bottle`.
2. `migrations/versions/0009_bottle_processing_stage.py` — new migration; bump `HEAD_REVISION` in
   `bourbonbook/migrations.py`.
3. `bourbonbook/bottle_processing.py` — new module: `BottleProcessingStage`, `IN_PROGRESS_STAGES`,
   `run_add_bottle_pipeline`, `recover_orphaned_bottle_processing`.
4. `bourbonbook/main.py` — import the new module; call `recover_orphaned_bottle_processing` in
   `lifespan`; rewrite `add_bottle` to insert-then-`background_tasks.add_task(...)`-then-return
   JSON; add `GET /bottles/{bottle_id}/status`; add the `processing_stage` exclusion to
   `collection_statement`.
5. `bourbonbook/templates/new.html` — add the stage-text and error-banner elements.
6. `bourbonbook/static/app.js` — replace the submit handler with the fetch+poll version.
7. Update `tests/test_app.py`'s `test_add_review_edit_and_view_bottle` for the new 202/JSON
   contract; add the failure-path and collection-exclusion cases.
8. Add `tests/test_bottle_processing.py` for the pipeline function and the recovery sweep.
9. Extend `tests/test_migrations.py` for the new column/index/default.
10. `pytest -xvs`; manually exercise `/bottles/new` against a real Ollama host to confirm the
    stage text visibly changes (analyzing → getting bottle details → checking pricing) and the
    final redirect lands on the edit page with the same banner behavior as before.
