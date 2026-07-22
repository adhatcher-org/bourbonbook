# Admin Catalog Upload + Phase 2 Local-AI Plan

## Audited status — 2026-07-21

This is the execution plan and audit for the workflow described in
[`admin-upload-plan.md`](admin-upload-plan.md) and the RTX 3090 questions in
[`Phase 2 Updates.md`](Phase%202%20Updates.md).  It supersedes the earlier
assumption that neither plan had implementation evidence.

**Status vocabulary:** **implemented** means committed, wired, and supported by
the named evidence; **partial** means usable seams or uncommitted work exist,
but the acceptance criterion is not met; **missing** means no scoped feature is
present; **blocked** means an external decision/authorization is required.
Uncommitted files are never treated as shipped functionality.

### Evidence boundary

- **Committed baseline (`HEAD` `4e70330`):** `main.py:1324-1355` has an
  admin- and CSRF-protected upload form, but accepts only declared content types
  and returns the explicit placeholder “extraction review will be available in
  the next update.” `catalog_extract.py` plus
  `scripts/extract_catalog_screenshots.py` provide CLI-only local extraction;
  `catalog_cli.py` and `qdrant_prices.py` provide catalog/Qdrant primitives.
- **Current working tree, not committed and not validated as a unit:** changes
  to `benchmark_cli.py`, `catalog_extract.py`, `Makefile`, and their tests;
  untracked `model_evaluation.py`/test; and untracked
  `bourbonbook/migrations/versions/001_catalog_import_batches.py`. The latter
  is not in the real Alembic tree (`migrations/versions`), starts a second root
  (`down_revision = None`), uses PostgreSQL JSONB despite the SQLite deployment,
  and has no ORM or bootstrap/test wiring. It is not a usable migration.
- **Quality evidence:** `Makefile:43-47` defines deterministic tests and a
  temporary repository-wide 80% branch-coverage gate. AUP2-13 restores and
  enforces the 90% final gate. The required focused changed-area target is
  **80%** for upload/extraction/batch/apply/queue code.

## Product boundaries retained from the source plans

- A local vision model may propose only visible `name`, `size`, and displayed
  current price from a local upload; the administrator supplies the update date.
  `Now $x was $y` means `Now $x`. No browser, OHLQ, OpenAI, remote provider,
  credential, or URL lookup belongs in this workflow.
- Exact normalized `(name, size)` is the shared `CatalogPrice` identity. Import
  never creates a user `Bottle`; purchase-price six-month behavior remains
  separate. SQLite is authoritative if Qdrant is unavailable.
- The RTX 3090 is an accepted deployment decision. Aaron explicitly waived
  performance/accuracy benchmarking as a promotion gate; that is a product
  decision, not benchmark evidence. Keep at most one large application model
  resident in an interactive path; the coding model stays excluded from the
  application request path. Future live measurements are optional diagnostics.

## Ordered atomic actions

| ID | Status | Audited evidence and corrected scope | Dependency / exit criterion |
| --- | --- | --- | --- |
| AUP2-00 | **implemented** | This audit identifies configuration (`config.py`), auth/CSRF (`main.py`), migration bootstrap (`migrations.py`), catalog/Qdrant (`catalog_cli.py`, `qdrant_prices.py`), and the placeholder route. It also records the dirty-tree boundary above. | Complete. Preserve unrelated changes. |
| AUP2-01 | **validated, uncommitted** | Report v2 semantics, operation-scoped fields, strict canonical comparison, `verified` success, bounded evidence, and legacy rejection are independently validated at 93.07% focused coverage. | Complete for implementation; temporary aggregate gate is 80% until AUP2-13 restores 90%. |
| AUP2-02 | **validated, uncommitted** | The local-only boundary is independently validated: `--live`, forced Ollama, cleared OpenAI key, fake OpenAI non-reachability, bounded timeout/failure evidence, and cancellation propagation. | Complete for implementation; temporary aggregate gate is 80% until AUP2-13 restores 90%. |
| AUP2-03 | **waived by product decision** | Aaron accepted RTX 3090 deployment without a performance/accuracy benchmark. No live run occurred, no model was pulled, and production defaults were not changed. | Future live runs are optional diagnostics only. |
| AUP2-04 | **decided; implementation deferred to dependent actions** | The durable SQLite queue, one-worker/GPU-lane topology, capacity, timeout, retry, lease, retention, and source-cleanup defaults are approved below. No queue/job module exists yet. | AUP2-05 supplies persistence; AUP2-08 wires the worker. |
| AUP2-05 | **partial** | The untracked migration shows intended fields, but is in the wrong directory/root and is incompatible with the current Alembic chain/SQLite. No `CatalogImportBatch`/proposal ORM, repository, constraints, or migration tests exist. | After AUP2-04's durable-work decision, create one additive revision from `0007_catalog_prices`, SQLite-compatible storage, models, and empty/upgrade tests. |
| AUP2-06 | **partial** | Existing bottle upload infrastructure and `MAX_UPLOAD_MB` provide patterns, but catalog import persists nothing and validates only `UploadFile.content_type` (`main.py:1336-1354`). No generated names, signature/decode checks, aggregate/page limits, ownership, or lifecycle cleanup exists. | Requires configured limits/expiry and the batch ownership model from AUP2-05. |
| AUP2-07 | **partial** | `catalog_extract.py` parses/deduplicates records and the committed CLI renders PDFs/chunks locally, but the route cannot call it and reusable rendering/Ollama request code stays in `scripts/extract_catalog_screenshots.py`. No package service or bounded model-error mapping exists. Current dirty logging must be reviewed for redaction, not assumed compliant. | Refactor behind a package API after upload staging; deterministic image/PDF/fake-Ollama tests only. |
| AUP2-08 | **partial** | The POST has verified-admin/CSRF protection but only validates types and renders a placeholder. There is no persisted batch, worker, retry/idempotency, state transition, or safe lifecycle metrics. | Requires AUP2-04 through AUP2-07. It must create `extracting` then move to `review`/bounded `failed`, never write `CatalogPrice`. |
| AUP2-09 | **partial** | The committed upload page and menu link exist, but there are no recent batches, status/review route, editable rows, pagination, inclusion mutations, or visual/accessibility coverage. | Requires persisted batches and orchestration (AUP2-08); use the PWA visual-check workflow. |
| AUP2-10 | **partial** | `catalog_cli.ingest_jsonl` has normalized-key upserts, but is CLI-only, has no review state/atomic batch apply/counts/delete tests, and calls Qdrant before SQL commit (`catalog_cli.py:73-112`). | Requires review persistence/UI. Apply must be one SQL transaction; Qdrant moves post-commit in AUP2-11. |
| AUP2-11 | **partial** | `QdrantPriceIndex` is optional and logs bounded HTTP failures, but batch reindex status/retry and import metrics do not exist. Current CLI ordering is not post-commit. | Follows atomic apply. Reindex only created/updated IDs after commit; record retryable result without invalidating SQL. |
| AUP2-12 | **partial** | `admin-upload-plan.md` documents desired retention/governance and the committed extractor is local-file oriented, but no batch/source cleanup, operator runbook, queue/volume/limit defaults, or first-import procedure is implemented. | Follows lifecycle implementation. First authorized production-like import is `AmericanWhiskey1.png` only; leave `AmericanWhiskey2.png` for manual admin testing. |
| AUP2-13 | **missing** | Commands exist: `make test`, `make lint`, `make coverage`, `make build`, and `make pr-review`; temporary coverage configuration enforces 80%. No focused changed-area measurement, current successful full gate, migration/visual evidence, candidate commit, or commit-bound reviewer/validator result exists. | Last. Restore the configured 90% gate, then require focused >=80% plus full `make coverage` >=90%, the exact-commit independent review, and `make pr-review` validation required by `AGENTS.md`. |

## Corrected dependency order

1. **AUP2-01 → AUP2-02**: finish the deterministic P2-00 benchmark contract
   and local-only controls, including review of the existing uncommitted work.
2. **AUP2-03** is waived as a performance gate. It is not a prerequisite for
   secure import plumbing; any later live measurement is diagnostic only.
3. Decide AUP2-04 operational values and durable-worker design, then implement
   **AUP2-05 → AUP2-06 → AUP2-07 → AUP2-08 → AUP2-09 → AUP2-10 → AUP2-11 →
   AUP2-12 → AUP2-13**. This corrects the old ordering: staging needs a batch
   owner/lifecycle, orchestration needs the queue and extraction service, review
   needs persisted proposals, and Qdrant must follow—not precede—SQL commit.

## Required external decisions / authorizations

- Before AUP2-04/AUP2-06: choose latency/error budgets, interactive versus
  extraction concurrency, queue capacity, timeout/retry limits, file/total/page
  limits, retention/expiry, and durable in-process versus existing job mechanism.
- Before first production-like import: authorize the local deployment and
  `AmericanWhiskey1.png` fixture only. This does not authorize a PR, push, or
  any remote provider.

## AUP2-04 proposed operating decision

### Options considered

| Option | Strengths | Costs / reason not selected |
| --- | --- | --- |
| Request-scoped background task or memory-only `asyncio.Queue` | Smallest code change. | Loses queued work on restart, has no durable audit state, and cannot safely power a review-first import workflow. |
| External Redis/Celery/RQ worker | Strong multi-worker and multi-host scaling story. | Adds containers, persistent broker state, monitoring, and deployment complexity before this single-worker SQLite/Unraid application needs it. |
| **SQLite-backed queue with one in-process worker** | Durable state and restart recovery; no new service; aligns with the existing one-Uvicorn-worker rule; keeps SQLite authoritative. | Deliberately single-host/single-worker. It must use leases and never execute a long job in the request handler. |

### Recommended decision: durable SQLite queue, one GPU lane

Implement the catalog-import queue as durable `CatalogImportBatch` state in SQLite. The FastAPI
lifespan starts one worker loop in the existing application process. A successful upload transaction
persists a `queued` batch before the HTTP response; the worker claims it with a lease, runs local
extraction outside the request, and transitions it to `review` or bounded `failed`. Startup requeues
expired `extracting` leases. There are no additional workers or replicas while SQLite is the write
store.

Use one global GPU lane with capacity **1**. Catalog imports never run concurrent local-model calls;
the queue can later share that lane with interactive analysis, but this action must not silently
convert existing bottle analysis into a background job. There is no unsafe preemption: a request
already using the GPU finishes, and the next eligible job starts in priority order.

### Approved operating defaults

| Setting | Proposed value | Rationale |
| --- | --- | --- |
| Active extraction jobs | 1 | One large model resident on the 3090; protects VRAM and SQLite's single-writer deployment. |
| Waiting catalog-import batches | 5 | Gives an administrator room to stage work without turning the app into an unbounded file store. |
| Per batch | 5 files, 50 MiB total, 10 PDF pages | Bounds disk, rendering work, and an accidentally oversized catalog submission. Individual file limits still respect `MAX_UPLOAD_MB`. |
| Per model chunk timeout | 120 seconds | Long enough for a 26B local vision model; bounded so a stuck request does not hold the lane forever. |
| Batch deadline | 15 minutes | Covers multi-page processing while providing a clear failure/retry boundary. |
| Automatic retries | 1 for transient local-model/transport failures | Avoids repeated GPU work; all other failures require an admin retry from the review/status page. |
| Lease / recovery | 20-minute lease with periodic heartbeat | Reclaims interrupted work after a container restart without double-processing an active batch. |
| Uploaded source retention | Keep only while queued/extracting; delete immediately after proposals or terminal failure are persisted | Reprocessing is not in scope, so review relies on persisted normalized proposals rather than retaining the source upload. |
| Batch audit summary retention | 90 days after apply/failure/expiry | Keeps bounded operational evidence without retaining source files or raw model output. |

### AUP2-04 implementation plan after confirmation

1. Add queue configuration, typed limits, and startup validation; document Docker/Unraid environment
   variables and preserve safe defaults.
2. Add the reusable database claim/lease/state-transition contract and deterministic tests for FIFO
   order, capacity rejection, lease recovery, cancellation, transient retry, terminal failure, and
   no duplicate claim. This is the only generic job infrastructure needed now.
3. Add a lifespan-owned single worker that uses the durable claim contract and a capacity-one GPU
   lane. It records bounded queue wait, model duration, render duration, attempts, and outcome only,
   then removes source files immediately after persisting proposals or terminal failure.
4. Add authenticated admin status polling/refresh plumbing only after AUP2-05 through AUP2-08 create
   the batch records and extraction service; do not create a parallel in-memory status store.
5. Test with fakes and generated files only. Require >=80% focused coverage. The temporary 80%
   repository-wide gate applies for implementation; AUP2-13 restores the 90% final gate alongside
   migration and visual checks.

## Next eligible implementation action

**AUP2-05: create the SQLite-compatible catalog-import persistence foundation.**
The AUP2-04 operating values are approved. Replace the invalid untracked
migration with one additive revision in the real Alembic chain, along with the
ORM, repository/state contract, and fresh/upgrade migration tests. Do not reuse
the untracked migration: its location, revision ancestry, database type, and
ORM integration are invalid.

## Promotion policy

Deterministic tests must use generated fixtures and fakes—never a GPU, model
pull, credentials, network, or private upload. Aim for **>=80% focused coverage**
of changed import/extraction/batch/apply/queue paths. During this approved
implementation sequence, PR promotion requires **`make coverage` >=80%**;
AUP2-13 restores the 90% gate before final validation. The remaining checks are
test, lint/format, build, migration, and visual
checks. Before any authorized draft PR, `AGENTS.md` requires a candidate commit
followed by a commit-matching `bourbonbook_reviewer` PASS and `pr_validator`
local PASS running `make pr-review`; fixes require a new candidate and fresh
both checks.
