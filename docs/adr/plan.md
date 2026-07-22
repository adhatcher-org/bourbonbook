# Bourbon Book TODO Implementation Plan

## Purpose

This plan records the remaining Bourbon Book roadmap as a sequence of independently reviewable
actions. Only one action is implemented in a branch and Codex session at a time. Every action must
finish with tests, an independent sub-agent review, `make pr-review`, a pushed branch, and a draft
pull request before its status changes from **Incomplete** to **Complete**.

The remaining work is intentionally RAG-infrastructure-first. A disposable prototype will prove
Ollama embeddings, Qdrant collection management, fixture loading, metadata filtering, and raw
vector search before the application commits to production pricing sources or recommendation
rules. After that prototype is measured, the downstream pricing roadmap must be reviewed again.

## Superseded: Benchmark-Gated Model Selection

[ADR 0003](0003-fixed-local-model-no-benchmark-gate.md) retires P2-00 and P2-01 as blocking
prerequisites. Local model selection for the photo-analysis and name-reconciliation roles is now a
fixed operator/configuration decision (`qwen3.6:35b` for both `OLLAMA_VISION_MODEL` and
`OLLAMA_MODEL`, per README/.env.example) rather than something gated on a passing
`benchmark_cli`/`model_evaluation` acceptance record. The hardware (RTX 3090) and provider (Ollama)
were already fixed regardless of any benchmark outcome, so the gate no longer had a decision left to
inform. P2-00/P2-01 rows below are marked **Retired** rather than deleted, to preserve the record of
what was attempted; `benchmark_cli.py`/`model_evaluation.py` remain in the repository as optional,
non-blocking diagnostic tooling. See ADR 0003 for full rationale and consequences.

## Confirmed Decisions

1. Removing the HTML `capture="environment"` hint will restore the normal iPhone image chooser;
   actual camera, Photo Library, PWA, and HEIC behavior must be verified.
2. Form, search, select, price, and quantity values use self-hosted **Atkinson Hyperlegible Next**
   at `1.05rem` with a `1.45` line height for readability. Button typography remains unchanged.
3. A versioned JSON fixture is authoritative only for the disposable RAG prototype. It contains
   current bottle identities plus clearly labeled synthetic price documents and can rebuild Qdrant.
4. Production SQL schemas, price sources, evidence policies, and recommendation rules are deferred
   until the RAG infrastructure has been configured, loaded, queried, and reviewed.
5. A manual MSRP or suggested secondary-price edit creates a durable, prioritized refresh job.
6. A successful source-backed refresh automatically replaces the displayed manual price. The
   superseded manual value remains in price history with `user_reported` provenance.
7. Users may immediately blacklist a source for their own results and may suggest a new source.
   Only an administrator may activate, disable, or globally blacklist crawl sources.
8. OpenAI proposes candidate sites and extracts structured evidence. Model-proposed sites require
   administrator approval before the server accesses them.
9. Sources requiring an authenticated human session use an explicit manual-import workflow. The
   system must not bypass a paywall, firewall, access control, or site terms.
10. The existing `tests/images` identities seed the prototype corpus. Synthetic prototype prices
    must never populate bottle fields or be represented as sourced production evidence.
11. Bourbon Book continues to run as one Uvicorn worker in Docker on Unraid. The durable pricing
    worker runs in that process until the application adopts a database suited to multiple writers.
12. Development environments always capture email locally and expose the generated verification
    link on the check-email page. SMTP delivery is restricted to production.
13. The prototype stops at raw filtered vector search. Ollama synthesis, OpenAI fallback, source
    discovery, crawling, production ingestion, and user-visible recommendations remain deferred.
14. Development supports Aaron's external Unraid Qdrant by URL plus an optional local Compose
    service. Qdrant must not be exposed through the public Bourbon Book route.
15. Admin catalog imports use a SQLite-backed durable queue with one lifespan-owned worker in the
    existing single-Uvicorn-worker process. The database is the source of truth: a request commits
    a queued batch before responding; a leased worker claims it; startup recovers expired leases.
    Redis, Celery, RQ, extra workers, and replicas are out of scope while SQLite is the write store.
16. Local-model work has one GPU lane with capacity one. Catalog imports do not run concurrent
    model calls, do not preempt active work, and must later yield priority to interactive analysis.
    The approved catalog-import defaults are: five waiting batches; five files, 50 MiB total, and
    ten PDF pages per batch; 120-second chunk timeout; 15-minute batch deadline; one automatic
    retry for transient failures; and a 20-minute leased job with heartbeat/recovery.
17. Catalog-import source files are retained only while queued or extracting and are deleted
    immediately after normalized proposals or a terminal failure are persisted. Reprocessing is not
    in scope. Batch audit summaries (without source files, raw model output, or prompts) are
    retained for 90 days.
18. The repository-wide branch-coverage gate is temporarily **80%** while the approved AUP2 upload
    sequence is under implementation. AUP2-13 must restore and pass the **90%** gate before final
    validation, pull-request promotion, or release.

## Action Tracker

| ID | Action | Status | Branch | Pull request / completion evidence |
| --- | --- | --- | --- | --- |
| A01 | Restore iPhone Photo Library selection | Complete | `codex/iphone-photo-picker` | [PR #11](https://github.com/adhatcher-org/bourbonbook/pull/11); sub-agent clean; `make pr-review` passed. |
| A02 | Apply Atkinson Hyperlegible Next to edit controls | Complete | `codex/readable-edit-font` | [PR #12](https://github.com/adhatcher-org/bourbonbook/pull/12); desktop/iPhone and missing-font fallback verified; sub-agent clean; `make pr-review` passed. |
| P2-00 | Repair benchmark semantics, runtime evidence, and local-only enforcement | **Retired — no longer required (ADR 0003)** | `feature/p2-benchmark-contract` | Historical: `p2_00_benchmark_implementer`/`validator` passed focused 92.20% coverage; repository aggregate 86.80% never reached the 90% PR-promotion floor. Retired as a blocking prerequisite by ADR 0003; not needed to adopt or change a local model. |
| P2-01 | Select local model roles on the 3090 | **Retired — no longer required (ADR 0003)** | `feature/p2-model-evaluation` | Historical: offline implementer/validator pass (149 tests, 90% focused coverage); the required "authorized 3090 report" was never produced. Retired by ADR 0003 — model roles are now a fixed config decision (`qwen3.6:35b` for both photo and name), not a benchmark-gated selection. |
| P2-02A | Harden local evidence-to-catalog analysis contract | Blocked by P2-01 | `feature/p2-local-analysis-contract` | `p2_02a_analysis_implementer` then `p2_02a_analysis_validator`; catalog is authoritative and unresolved facts remain reviewable. |
| P2-02B | Add bounded GPU scheduling and timing telemetry | Blocked by P2-02A | `feature/p2-gpu-queue-telemetry` | `p2_02b_queue_implementer` then `p2_02b_queue_validator`; one-large-model residency and deterministic timing tests required. |
| P2-02C | Add durable analysis jobs, progress, and confirmation UI | Blocked by P2-02B | `feature/p2-analysis-jobs-ui` | `p2_02c_jobs_ui_implementer` then `p2_02c_jobs_ui_validator`; fresh/upgrade migrations, owner security, browser tests, and ≥80% focused coverage required. |
| P2-03A | Establish exact source-backed price evaluation contract | Blocked by P2-02C | `feature/p2-price-evaluation-contract` | `p2_03a_price_contract_implementer` then `p2_03a_price_contract_validator`; separate pricing fixture and provenance gate required. |
| P2-03B | Replace runtime LLM price lookup with direct evidence | Blocked by P2-03A | `feature/p2-direct-price-refresh` | `p2_03b_price_source_implementer` then `p2_03b_price_source_validator`; exact matching and no-OpenAI-path tests required. |
| P2-04 | Remove OpenAI runtime paths | Blocked by P2-02C / P2-03B | `feature/p2-remove-openai` | `p2_04_openai_removal_implementer` then `p2_04_openai_removal_validator`; prove local-only operation and ≥80% focused coverage. |
| A03 | Configure prototype RAG infrastructure | Incomplete | `codex/rag-infrastructure` | No Qdrant/Ollama embedding module, configuration, CLI, dependency, or Compose profile is present. |
| A04 | Load and search the prototype corpus | Incomplete | `codex/rag-prototype-index` | No prototype corpus, loader, raw search command, or Qdrant substrate is present; blocked by A03. |
| A05 | Reconcile and extend the pricing evaluation fixtures | Deferred | `codex/pricing-evaluation-fixtures` | Phase 1 added a private benchmark fixture/CLI, but MSRP is reference-only and unscored; production pricing evaluation remains deferred. |
| A06 | Add the pricing evidence schema and separate provider roles | Deferred | `codex/pricing-evidence-foundation` | `CatalogPrice` cache/migration and local/OpenAI analysis roles exist, but the governed evidence, job, and provider-role schema is not implemented. |
| A07 | Add governed source administration and user preferences | Deferred | `codex/pricing-source-registry` | No source registry, user preferences, administrator source UI, or source-governance validation is present. |
| A08 | Add durable refresh jobs and automatic manual-price replacement | Deferred | `codex/pricing-refresh-jobs` | A synchronous 90-day OHLQ cache/refresh exists; no durable job, worker, priority, retry, or price-history replacement is present. |
| A09 | Add OpenAI-assisted source discovery | Deferred | `codex/pricing-source-discovery` | Existing OpenAI use is a per-bottle grounded price search; no discovery, candidate approval, or decision history is present. |
| A10 | Add scheduled fetching and OpenAI evidence extraction | Deferred | `codex/pricing-evidence-ingestion` | Existing synchronous price search is not scheduled fetching, source adaptation, or durable evidence ingestion. |
| A11 | Add Qdrant indexing and Ollama-first price retrieval | Deferred | `codex/qdrant-ollama-pricing` | Local catalog/OHLQ cache exists, but no Qdrant embedding, index, filtered retrieval, or Ollama evidence synthesis is present. |
| A12 | Add user-authorized manual and browser-assisted imports | Deferred | `codex/manual-source-import` | No import session, authorized upload route, artifact parser, or browser-assisted helper is present. |
| A13 | Complete end-to-end evaluation and Unraid operations | Deferred | `codex/pricing-pipeline-validation` | Phase 1 benchmark tooling and current Docker health documentation exist; the finished pricing-pipeline evaluation and operations gate is outstanding. |

## Implementation Audit

Audited against committed `4e70330` on 2026-07-21. “Partial” below identifies reusable
foundations only; it does not satisfy an action's dependencies, completion evidence, or lifecycle.

### Phase 2 audit

- **P2-00 — Outstanding as of this audit; retired by ADR 0003.** `benchmark_cli.py` writes a v1
  report, counts only `complete` status as success, scores all fields for both operations, permits
  fuzzy name matching, and has no runtime evidence or local-only provider guard. Catalog-backed
  analysis can return `verified`, so current benchmark results are not decision-ready. This gap is
  no longer a blocker for model selection — see [ADR 0003](0003-fixed-local-model-no-benchmark-gate.md).
- **P2-01 — Partial foundation as of this audit; retired by ADR 0003.** Vision/text model settings
  and photo-aware model selection exist, but defaults were never accepted through the repaired 3090
  role benchmark described here. ADR 0003 retires that requirement: model roles are now a fixed
  config decision (`qwen3.6:35b` for both), not benchmark-gated.
- **P2-02A/B/C — Partial foundation.** Local extraction, catalog matching, a text reconciliation
  path, and synchronous request handling exist. The confidence/evidence contract, bounded residency
  queue, durable work, progress surface, low-confidence confirmation, and timing lifecycle are
  outstanding.
- **P2-03A/B — Partial foundation.** `CatalogPrice`, an import CLI, and optional Qdrant lookup exist,
  but the lookup is fuzzy and a cache miss can use OpenAI web search. The separate source-backed
  pricing benchmark, exact canonical identity/provenance gate, and direct-only refresh are
  outstanding.
- **P2-04 — Outstanding.** OpenAI provider/runtime/configuration/admin/docs/dependency paths remain.

Uncommitted work in `bourbonbook/catalog_extract.py`, `bourbonbook/migrations/`, `tests/tmp/`, and
`.vscode/` is excluded from this audit and must not be treated as Phase 2 completion evidence.

- **A03 — Outstanding.** No application Qdrant client, `/api/embed` boundary, RAG settings/admin
  fields, RAG command module, Qdrant dependency, or Compose `rag` profile exists.
- **A04 — Outstanding.** There is no versioned synthetic prototype corpus, validator, loader,
  raw filtered-search service, or smoke workflow. A03 has not supplied the required collection.
- **A05 — Partial foundation; deferred.** [`bourbonbook/benchmark_cli.py`](../../bourbonbook/benchmark_cli.py)
  exports a private owner-scoped fixture and scores photo/name fields, while MSRP is explicitly
  reference-only. It is not a source-backed pricing-evaluation corpus or provenance contract.
- **A06 — Partial foundation; deferred.** [`CatalogPrice`](../../bourbonbook/models.py) and migration
  `0007_catalog_prices` provide a shared OHLQ MSRP cache; [`PriceSource`](../../bourbonbook/models.py)
  remains bottle-scoped URL metadata. There are no durable product/evidence observations, currency
  fields, refresh jobs, or finalized local-only provider interfaces.
- **A07 — Outstanding.** No global source records, per-user blocks/suggestions, administrator
  source controls, source audit trail, or SSRF-aware source-administration boundary exists.
- **A08 — Partial foundation; deferred.** [`refresh_prices`](../../bourbonbook/main.py) performs an
  immediate cache lookup or synchronous provider request. It has no durable queue, lease/retry
  worker, generation check, manual-value history, or automatic replacement workflow.
- **A09 — Outstanding.** [`bourbonbook/openai_provider.py`](../../bourbonbook/openai_provider.py)
  supports a current per-bottle price search only; it does not discover, validate, queue, or require
  approval for source candidates.
- **A10 — Outstanding.** No scheduled worker, per-source fetch adapter, robots/rate policy,
  fixture-page extractor, evidence fingerprint, or observation ingestion exists.
- **A11 — Partial local cache only; deferred.** The verified product catalog and OHLQ cache reduce
  repeated lookups, but the repository contains no Qdrant client, embedding calls, vector index,
  evidence filter, or Ollama-grounded price retrieval.
- **A12 — Outstanding.** No manual-import model, one-time import token, authorized artifact upload,
  import parser, or Playwright helper exists.
- **A13 — Partial foundation; deferred.** The private benchmark commands and existing Docker health
  check are usable inputs, but no end-to-end pricing-pipeline suite, operational runbook, Qdrant
  recovery test, rollout checklist, or final acceptance report exists.

## Phase 2 Local-AI Direction and Ordered Implementation

This direction supersedes the unimplemented OpenAI-dependent guidance in confirmed decisions 8 and
13 and in the historical A06/A09/A10/A11/A13 text. Those actions remain deferred and must be
rewritten before implementation; no new Phase 2 branch may add an OpenAI fallback, discovery call,
or evidence-extraction call.

| Order | Action | Scope and acceptance gate |
| --- | --- | --- |
| 1 | **P2-00 — Correct benchmark semantics and capture 3090 evidence (retired, ADR 0003)** | No longer a prerequisite for model selection. The scoring/semantics defects described here (success counting, field observability, cold-start capture) remain real if the benchmark tooling is ever used again for ad hoc comparison, but fixing them is no longer required before adopting or changing a local model. |
| 2 | **P2-01 — Select local model roles (retired, ADR 0003)** | No longer benchmark-gated. `qwen3.6:35b` is adopted directly for both `OLLAMA_VISION_MODEL` and `OLLAMA_MODEL` (photo and name-reconciliation roles) as a fixed configuration decision — see [ADR 0003](0003-fixed-local-model-no-benchmark-gate.md). `benchmark_cli.py`/`model_evaluation.py` remain available for optional, non-blocking ad hoc comparison only. |
| 3 | **P2-02 — Build the local photo, catalog, and job pipeline** | Implement the operation-specific local flow below: one VLM photo job followed by local catalog facts; run the general text model only after a catalog miss/ambiguity. Add bounded queue/model-residency telemetry, durable jobs, visible progress, and low-confidence user confirmation. Keep one large application model resident; Continue's `qwen3-coder:30b` is development-only. |
| 4 | **P2-03 — Replace LLM price lookup with direct evidence** | Preserve the OHLQ cache but replace LLM web search with an exact product/size direct-source or imported-catalog adapter. Store URL, observation date, source basis, and freshness; return unavailable rather than inventing MSRP. Run a separate source-backed price evaluation. |
| 5 | **P2-04 — Remove OpenAI runtime paths** | Prove fake-provider tests and usage records cannot call OpenAI; remove the fallback, price-search adapter, configuration, client lifecycle, admin controls, dependency, and stale documentation only after P2-02/P2-03 pass. |
| 6 | **A03 then A04 — Prototype RAG** | Resume the standalone Qdrant/embedding prototype after the local analysis cutover is stable. It remains diagnostic/raw and must not reintroduce OpenAI or production price recommendations. |
| 7 | **Post-RAG checkpoint, then A05–A13** | Rewrite every deferred pricing action around the local-only provider policy, source governance, and direct evidence ingestion before beginning it. (No longer gated on a completed Phase 2 model benchmark — retired by ADR 0003.) |

### Phase 2 model and transaction rules

Updated by [ADR 0003](0003-fixed-local-model-no-benchmark-gate.md): model roles are a fixed
configuration decision, not a benchmark-selected candidate list. The rules below reflect the
current configuration, not the retired candidate/challenger framing.

- `qwen3.6:35b` is the fixed model for both roles: photo analysis (label text, identity, status,
  and fill level) and name-only/catalog-miss reconciliation. OCR text is not evidence of fill
  level; uncertain visual estimates require review. Exact local catalog matches return without an
  LLM call in either role.
- `qwen3-coder:30b` belongs in Continue for development, never the application request path. Unload
  it before app analysis so the 24 GB card does not co-reside competing large models.
- Pricing is a separate direct-source/cache job and must never delay photo or name analysis.

### Local operation map and user-transaction timing

| User operation | Normal local path | Exception path | User-visible timing and model residency |
| --- | --- | --- | --- |
| Add a bottle from a photo | Normalize image → `qwen3.6:35b` reads label/identity and visual facts → exact local-catalog match supplies durable product attributes → save a reviewable result. | If the catalog cannot match or the identity is ambiguous, queue `qwen3.6:35b` reconciliation using only the image-derived evidence; leave unresolved facts for user correction if it cannot establish a match. | Show photo-analysis progress. Since photo and reconciliation now share one fixed model (`qwen3.6:35b`, ADR 0003), no load/evict swap is needed between them. A low-confidence fill-level or status estimate still requires confirmation rather than an automatic overwrite. |
| Add or update bottle attributes from a typed name | Exact local-catalog lookup returns durable attributes without an LLM. | `qwen3.6:35b` reconciles a miss/ambiguity, then the catalog remains the authority for the saved attributes. This is a fixed configuration choice, not a benchmark-selected candidate (ADR 0003). | Return catalog matches immediately. Show a distinct reconciliation stage when it is needed. |
| Add or refresh MSRP | Apply a fresh exact product-and-size OHLQ cache entry immediately. | Queue/run a direct-source or imported-catalog refresh with URL, source basis, and checked date; return unavailable when there is no verified result. | Never load an LLM or block photo/name analysis. Surface the price source and timestamp separately from bottle analysis. |
| Continue-assisted coding | No application request-path work. | None. | `qwen3-coder:30b` is development-only and must be unloaded before user analysis or benchmark work so it cannot consume the 3090's model-residency budget. |

Initially, the application may have exactly one large model resident: `qwen3.6:35b`, shared by both
the photo job and any reconciliation job (no swap between roles since ADR 0003 fixed both roles to
the same model). P2-02, if resumed, must still record queue wait, model load/eviction, inference,
catalog-match, and price-refresh durations separately; it may add concurrency or preload only after
a measured 3090 VRAM and end-to-end latency evaluation.

### Changes applied to the original Phase 2 direction

- Replaced a generic larger-model recommendation with role-specific selection, later fixed by
  [ADR 0003](0003-fixed-local-model-no-benchmark-gate.md) to a single model for both roles:
  `qwen3.6:35b` for photo analysis and text reconciliation; `qwen3-coder:30b` only for Continue
  development.
- Replaced a possible multi-model, synchronous request flow with catalog-first transactions and a
  one-large-model residency rule. Model-load/eviction is now measured separately and exception work
  is visible to the user instead of silently delaying the primary operation.
- Replaced LLM-generated MSRP with a source-backed OHLQ cache/direct-import operation. It may use
  network evidence when a refresh is needed, but it may never use OpenAI or any LLM to infer a price.
- Established P2-04 as the only cutover endpoint: after P2-02 and P2-03 meet their gates, production
  runtime paths must make no OpenAI API calls and remove the associated fallback, configuration,
  client, dependency, and documentation.

## Required Lifecycle for Every Action

Each `Incomplete` or `In Progress` action must use this lifecycle without combining the next action
into the same branch. A `Deferred` action cannot begin until its named checkpoint is completed and
the tracker is updated with an approved scope.

1. Start from the current remote default branch after all required predecessor PRs have merged.
2. Confirm `git status --short`; preserve and do not stage unrelated user work.
3. Create the branch named in the tracker. If it already exists, inspect it before reusing it.
4. Change the tracker row to `In Progress` locally while working. Do not call the action complete yet.
5. Implement only the named action and its documentation/tests. Prefer extending existing modules
   over adding parallel implementations.
6. Run focused tests throughout development, followed by the relevant full local checks.
7. After the implementation agent finishes, start a **new validation/fix agent**. It must inspect
   the final diff, run focused tests and `make coverage`, report correctness/regressions/missing
   coverage/security/scope findings, and make only contained fixes in the named action. Every fix
   requires a regression test and a rerun of affected checks.
8. Do not begin the next action until the validation/fix agent reports passing tests and ≥80% focused
   coverage. Before a PR, the temporary repository-wide 80% `make coverage` gate and the separate read-only
   `bourbonbook_reviewer` and `pr_validator`
   commit-bound checks required by the project instructions.
9. Run `make pr-review`. Fix failures and rerun until it passes. This target includes lint,
   formatting checks, branch coverage, Bandit, dependency audit, repository-integrity checks,
   migration tests, Compose validation, and a production image build.
10. Inspect `git diff --check`, `git status --short`, and the complete staged diff. Stage only files
    belonging to the action and commit with a terse action-specific message.
11. Push the branch and create a **draft** pull request into the repository default branch. The PR
    body must explain the change, root cause, user impact, migrations/configuration, tests,
    sub-agent validation, and `make pr-review` result.
12. After the draft PR exists, update this tracker row to `Complete`, add the PR URL and validation
    evidence, commit that plan update, and push it to the same PR branch.
13. Create a new Codex session for the next `Incomplete` action. Its opening prompt must identify
    the action ID, branch, dependencies, relevant files, required lifecycle, and whether it must wait
    for a predecessor PR to merge. Do not begin the next action in the completed action's session.

## Cross-Cutting Requirements

- Preserve CSRF protection, authenticated owner scoping, administrator authorization, and existing
  session protections on every new browser route.
- Never log or commit API keys, passwords, session cookies, imported browser cookies, access tokens,
  raw credentials, or complete authenticated page contents.
- Treat URLs and page content from users, OpenAI, and crawled sites as untrusted input. Validate
  schemes and hosts, block local/private/link-local destinations, cap redirects and response sizes,
  and prevent DNS rebinding/server-side request forgery.
- Respect site terms and `robots.txt`; identify the application, use conservative per-domain
  throttling, and prefer official APIs, feeds, JSON-LD, or downloadable price books.
- Keep MSRP, retailer asking price, completed sale, auction result, and user-reported price as
  distinct evidence types. Never silently combine them.
- Require exact product/release/edition/size matching before applying a price. Store currency and
  observation date on every price record.
- Structured Outputs constrain OpenAI response shape but do not prove factual correctness. Validate
  values and provenance in application code before persistence.
- In the production design, keep SQL and Qdrant records linked by stable SQL IDs and keep SQL usable
  if Qdrant is down. The disposable A03/A04 prototype instead uses stable fixture document IDs.
- Make migrations forward-only and test both a fresh database and an upgraded copy-shaped legacy
  database.
- Update `.env.example`, admin configuration, README, Docker/Unraid instructions, metrics, and
  usage accounting whenever an action introduces runtime behavior or configuration.
- Do not send real requests to OpenAI, Ollama, Qdrant, or external sites from deterministic tests.
  Use injected fakes and captured fixtures.

---

## A01 — Restore iPhone Photo Library Selection

### Goal

Allow a user to choose an existing iPhone photo while retaining camera capture, preview, upload,
replacement, and server-side image validation.

### Dependencies

None.

### Expected Files

- `bourbonbook/templates/new.html`
- `bourbonbook/templates/edit.html`
- `bourbonbook/photos.py` only if HEIC verification proves support is required
- `tests/test_app.py`
- `tests/test_runtime_boundaries.py` only if image decoding changes

### Individual Implementation Instructions

1. Remove `capture="environment"` from the add and replace-photo file inputs. Retain
   `type="file"`, `accept="image/*"`, field names, required behavior on add, and preview hooks.
2. Add template/route regression assertions that both controls accept images and no longer force a
   capture device.
3. Confirm the existing preview JavaScript still works without modification.
4. Test JPEG and PNG uploads through add, replace, and re-analyze routes.
5. Manually verify iPhone Safari and the installed PWA offer Photo Library and camera choices.
6. Test a real iPhone HEIC selection. If Safari does not convert it to a format Pillow accepts, add
   a maintained HEIF decoder, normalize it through the existing JPEG pipeline, update the lockfile,
   add a fixture, and document the supported formats. Do not add that dependency speculatively.

### Completion Evidence

- Focused upload tests pass.
- Manual iPhone Safari and PWA result is recorded in the PR.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A01 tracker row is updated.

---

## A02 — Apply Atkinson Hyperlegible Next to Edit Controls

### Goal

Improve form-value readability with a self-hosted typeface and a modest text-size increase without
changing button typography or the surrounding layout.

### Dependencies

None; may proceed after A01 merges to keep the default branch linear.

### Expected Files

- New self-hosted WOFF2 assets under `bourbonbook/static/fonts/`
- Font license/attribution beside the assets
- `bourbonbook/static/app.css`
- template/static tests as appropriate

### Individual Implementation Instructions

1. Obtain Atkinson Hyperlegible Next from its official project distribution and verify that the
   files may be redistributed under the supplied license.
2. Add only the regular and weight variants actually used by edit controls; prefer WOFF2 and avoid
   a runtime dependency on Google Fonts or another CDN.
3. Define a dedicated `@font-face` name with `font-display: swap` and sensible system fallbacks.
4. Apply the family to form fields, library search and sort controls, authentication fields, and the
   compact price and quantity value controls; do not change labels or buttons.
5. Set value text to `1.05rem` with a `1.45` line height while preserving spacing, colors, focus
   styles, control dimensions, and the mobile layout.
6. Verify representative text, decimal values, punctuation, placeholders, selects, and multiline
   notes on iPhone and desktop. Check that missing-font fallback remains readable.

### Completion Evidence

- Font assets and license are tracked and served by the application.
- Form, search, sort, price, and quantity values use the new font and readable sizing while button
  typography remains unchanged.
- Visual verification is documented with desktop and iPhone screenshots.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A02 tracker row is updated.

---

## A03 — Configure Prototype RAG Infrastructure

### Goal

Prove that Bourbon Book can connect to Ollama and Qdrant, discover the embedding shape, and create a
compatible filtered-search collection without committing to a production pricing schema.

### Dependencies

A01 and A02 are complete. Begin from the current remote default branch and preserve the user-added
image fixtures and unrelated working-tree changes.

### Expected Files

- Qdrant/Ollama embedding client and CLI modules
- `bourbonbook/config.py` and `bourbonbook/admin_config.py`
- `pyproject.toml`, `uv.lock`, `.env.example`, and `compose.yaml`
- focused configuration, client, collection, and CLI tests
- README and Unraid deployment notes

### Public Configuration and Commands

- `QDRANT_URL`: required by RAG commands; external Unraid URL or Compose service URL.
- `QDRANT_API_KEY`: optional secret and never logged or rendered back to administrators.
- `QDRANT_COLLECTION`: defaults to `bourbonbook-prototype-v1`.
- `QDRANT_TIMEOUT_SECONDS`: bounded positive timeout.
- `OLLAMA_EMBEDDING_MODEL`: defaults to `qwen3-embedding:0.6b`.
- Provide `check` and `init` CLI commands under one RAG command module. `check` is read-only; `init`
  may create missing collection/index resources but must never delete or recreate an existing one.

### Individual Implementation Instructions

1. Add the maintained Python Qdrant client and reuse the existing `httpx`-based Ollama boundary.
   Call Ollama's current `/api/embed` endpoint rather than the legacy embeddings endpoint.
2. Validate and expose the new settings through managed admin configuration. The API key remains an
   optional masked secret; URL, timeout, collection, and model use the existing typed validation.
3. Make `check` verify Qdrant connectivity and request a probe embedding from Ollama. Report bounded
   actionable failures without logging URLs containing credentials, API keys, response bodies, or
   embedded fixture text.
4. Make `init` derive vector dimensions from the probe response and create a cosine collection with
   metadata recording collection schema version, embedding model, and vector dimension.
5. If the collection exists, verify model, dimension, distance, and schema compatibility. Fail with
   migration/reindex guidance on mismatch; never silently delete, resize, or overwrite it.
6. Before loading any points, create keyword/integer payload indexes for `document_id`,
   `document_type`, `product_key`, `synthetic`, `price_kind`, `currency`, `bottle_size_ml`, and
   `schema_version`.
7. Add an optional Compose `rag` profile with a pinned Qdrant image, named persistent volume,
   internal service networking, health check, and loopback-only host port for local tooling. Keep
   the external URL path supported for Aaron's existing Unraid Qdrant.
8. Document how to pull `qwen3-embedding:0.6b`, connect to external and Compose Qdrant instances,
   initialize the collection, protect Qdrant from public ingress, persist its data, and diagnose
   model/collection incompatibility.

### Completion Evidence

- Fake-client tests cover validation, secret handling, connectivity failures, dimension discovery,
  collection creation, compatibility checks, and idempotent payload-index creation.
- An opt-in Compose smoke check proves the collection can be created and survives Qdrant restart.
- No production price schema, fixture ingestion, vector query, or user-visible behavior is added.
- Sub-agent review and `make pr-review` pass; draft PR exists and A03 is updated.

---

## A04 — Load and Search the Prototype Corpus

### Goal

Load a rebuildable development corpus containing bottle identities and unmistakably synthetic price
evidence, then prove raw semantic search and structured Qdrant filtering without generating a price
recommendation.

### Dependencies

A03 must be merged and its collection initialization must work against either external or Compose
Qdrant.

### Expected Files

- versioned prototype corpus JSON and schema validation
- RAG fixture loader and raw search service/CLI
- deterministic loader, query, filter, and failure tests
- Compose smoke workflow and prototype operating documentation

### Prototype Corpus Interface

1. Use a reviewed JSON document with a top-level schema version and stable document records.
2. Each record contains `document_id`, `document_type`, `product_key`, `title`, searchable `content`,
   normalized identity metadata, `schema_version`, and `synthetic`.
3. Identity records derive from the nine image fixtures representing eight unique products and set
   `synthetic=false`.
4. Synthetic price records additionally contain `price_kind`, `amount`, `currency`, and
   `bottle_size_ml`; set `synthetic=true` and source label `development_fixture`.
5. Synthetic prices are test data only. They must not be persisted in application SQL, applied to
   bottles, exposed as sourced evidence, or described as accurate market values.

### Public Commands

- `load --fixture <path>` validates, embeds, and idempotently upserts the prototype documents.
- `search --query <text> [--limit N]` returns raw score, document ID, title, and payload metadata.
- Search supports optional filters for product key, document type, synthetic flag, price kind,
  currency, and bottle size.
- `status` reports compatibility, point count, embedding model, dimensions, and fixture schema.
- Fixture-scoped pruning may remove obsolete points owned by the same fixture/schema. No command may
  delete unrelated points or recreate the collection automatically.

### Individual Implementation Instructions

1. Validate the complete fixture before embedding anything. Reject duplicate IDs, malformed product
   keys, missing identity metadata, nonpositive synthetic amounts/sizes, invalid currencies, or
   synthetic price records lacking the development source label.
2. Use deterministic point IDs and store the embedding model/schema in every point so repeated loads
   update rather than duplicate data.
3. Embed document text and query text with the same configured model. Keep query construction in one
   testable function so later retrieval evaluation can revise it without rewriting storage code.
4. Apply Qdrant payload filters as part of vector search. Return an explicit unavailable result for
   Ollama/Qdrant failures and never fall back to OpenAI in this action.
5. Keep output diagnostic and raw. Do not add Ollama synthesis, application routes, bottle-price
   changes, source crawling, scheduled ingestion, or production recommendation logic.
6. Add an opt-in smoke flow that initializes Qdrant, loads the fixture, runs representative identity
   and synthetic-price searches, verifies filters, reloads idempotently, and confirms persistence.

### Completion Evidence

- Exact-product queries retrieve the corresponding identity document and distinguish releases.
- `synthetic=false` searches never return synthetic price records.
- Product, type, price-kind, currency, and size filters are covered with fake clients.
- Repeated loads do not duplicate points; outages and incompatible collections fail safely.
- Sub-agent review and `make pr-review` pass; draft PR exists and A04 is updated.

---

## Post-RAG Design Checkpoint

After A04, stop implementation and open a new planning session. Review retrieval relevance,
metadata filters, model resource usage on Unraid, collection operations, networking, restart
behavior, and the limits of the synthetic corpus. Then decide production data sources, evidence
types, freshness/provenance rules, SQL schema, ingestion methods, quality baselines, grounded
synthesis, fallback behavior, and user-visible price semantics. Rewrite and reactivate A05 onward
from those decisions; do not implement the deferred text unchanged.

---

## A05 — Reconcile and Extend the Pricing Evaluation Fixtures

### Status Gate

Deferred pending the post-RAG design checkpoint. The instructions below are historical candidate
scope and must be rewritten before implementation.

### Goal

Turn the existing bottle-image expectations into a stable identity-and-pricing evaluation baseline
before changing pricing behavior.

### Dependencies

A04 and the post-RAG design checkpoint.

### Expected Files

- `tests/images/ImageTestValidation.md`
- `tests/images/` fixtures
- `scripts/evaluate_ollama.py`
- `tests/test_evaluation.py`
- new pricing-evaluation fixture/schema if separation makes the tests clearer

### Individual Implementation Instructions

1. Reconcile the image inventory while preserving user assets.
2. Separate image-derived identity expectations from pricing expectations. A photograph must not be
   treated as evidence of a current price.
3. Define pricing provenance, freshness, currency, size, evidence type, and acceptance rules at the
   checkpoint before adding production expectations.
4. Extend evaluation only after the production retrieval and evidence interfaces are approved.

### Completion Evidence

- Completion evidence must be rewritten at the checkpoint.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A05 tracker row is updated.

---

## A06 — Add the Pricing Evidence Schema and Separate Provider Roles

### Status Gate

Deferred pending the post-RAG design checkpoint. The instructions below are historical candidate
scope and must be rewritten before implementation.

### Goal

Establish durable product, evidence, and job primitives while decoupling image analysis, local price
retrieval, and OpenAI fallback.

### Dependencies

A05 and an approved post-RAG production-data design.

### Expected Files

- `bourbonbook/models.py`
- new Alembic migration
- `bourbonbook/config.py`
- `bourbonbook/admin_config.py`
- `bourbonbook/analysis.py`
- focused models/config/migration tests
- `.env.example`, `README.md`

### Individual Implementation Instructions

1. Define the catalog, production evidence, refresh-job, and provider-role interfaces only after the
   checkpoint resolves sources, provenance, freshness, and price semantics.
2. Preserve existing `PriceSource` URLs, displayed values, and legacy database compatibility.
3. Keep SQL authoritative and Qdrant rebuildable once the production schema is approved.

### Completion Evidence

- Completion evidence must be rewritten at the checkpoint.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A06 tracker row is updated.

---

## A07 — Add Governed Source Administration and User Preferences

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation.

### Goal

Let administrators govern crawl sources while users can blacklist sources and submit candidates.

### Dependencies

A06.

### Expected Files

- `bourbonbook/models.py` and a new migration
- focused source-registry service module
- admin and user routes in `bourbonbook/main.py` or an extracted router
- new admin templates and profile/source-preference UI
- CSS and route/model tests

### Individual Implementation Instructions

1. Add global pricing-source records with canonical domain, display name, evidence capabilities,
   trust tier, enabled state, global block state, access mode, cadence, last result, and notes.
2. Add user source preferences for per-user blacklist decisions and source suggestions with pending,
   approved, and rejected status plus an administrator audit trail.
3. Add an administrator source screen to create, approve, edit, disable, and globally block sources.
4. Add a user screen to blacklist/unblacklist existing sources and suggest a domain. Users must not
   activate sources or edit global trust/cadence values.
5. Canonicalize domains, reject credentials/fragments/non-HTTP schemes, and validate destinations
   against SSRF restrictions before saving and again immediately before every request.
6. Apply global and per-user blocks to price selection and future OpenAI searches. Preserve price
   history from newly blocked sources, but stop using it for current recommendations.
7. Add CSRF, authorization, audit logging, pagination, duplicate-domain, and validation tests.

### Completion Evidence

- Permission tests prove ordinary users cannot activate sources.
- Source blocks affect selection without erasing history.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A07 tracker row is updated.

---

## A08 — Add Durable Refresh Jobs and Automatic Manual-Price Replacement

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation.

### Goal

Queue prioritized pricing refreshes when a user edits MSRP or suggested secondary price, and safely
replace displayed manual prices when fresh grounded evidence becomes available.

### Dependencies

A06 and A07.

### Expected Files

- pricing queue/worker service modules
- application lifespan wiring
- `bourbonbook/main.py`
- bottle detail/edit templates
- observability instrumentation
- queue, route, restart-recovery, and concurrency tests

### Individual Implementation Instructions

1. Detect semantic changes to MSRP and secondary price during bottle saves. Record each changed value
   as `user_reported` evidence before enqueueing a refresh.
2. Upsert one prioritized pending job per product/identity generation with reason
   `manual_price_change`; repeated edits update priority/generation rather than creating a storm.
3. Add explicit user and scheduled-staleness reasons and deterministic priority ordering.
4. Run a durable asynchronous worker from the existing single-process application lifespan. Lease
   work transactionally, recover expired leases after restart, cap retries, and use backoff.
5. Before applying results, verify the bottle still points to the same canonical identity and job
   generation. Never apply results for a renamed or rematched bottle.
6. When accepted source-backed evidence exists, calculate the current MSRP/secondary recommendation
   deterministically and replace the displayed manual value automatically. Retain the manual
   observation and record the replacement reason/time.
7. If refresh fails or evidence is inadequate, preserve the current value and expose a bounded
   pending/unavailable status without leaking internal errors.
8. Add job counts, duration, results, and failure metrics without high-cardinality labels.

### Completion Evidence

- Tests cover deduplication, priority, restart recovery, stale generations, automatic replacement,
  fallback preservation, and history retention.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A08 tracker row is updated.

---

## A09 — Add OpenAI-Assisted Source Discovery

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation.

### Goal

Use OpenAI web search to propose useful pricing domains without allowing model output to become an
automatic crawl instruction.

### Dependencies

A07 and A08.

### Expected Files

- OpenAI discovery service and Pydantic schemas
- source administration routes/templates
- configuration and usage accounting
- discovery tests with fake Responses API objects
- README/admin documentation

### Individual Implementation Instructions

1. Add an administrator-triggered and low-frequency scheduled discovery operation using the existing
   OpenAI Responses API integration.
2. Provide the model with desired evidence classes, current approved domains, rejected candidates,
   and active global blocks. Apply supported allowed/blocked domain filters where appropriate.
3. Require Structured Outputs containing domain, display name, supported evidence types, rationale,
   likely access mode, and candidate URLs. Treat every field as an untrusted proposal.
4. Canonicalize, deduplicate, safety-check, and persist results only as pending suggestions. Never
   fetch or enable a discovered domain in this action.
5. Add administrator approve/reject controls and retain decision history so rejected sites are not
   repeatedly suggested without new justification.
6. Record usage/failure telemetry and add cost/rate controls so discovery cannot run on every bottle
   refresh.

### Completion Evidence

- Tests prove discovered sites remain pending until administrator approval.
- Global blacklist and rejected-source behavior is covered.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A09 tracker row is updated.

---

## A10 — Add Scheduled Fetching and OpenAI Evidence Extraction

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation.

### Goal

Fetch approved public sources conservatively and use OpenAI to convert relevant page data into
validated, idempotent pricing observations.

### Dependencies

A07–A09.

### Expected Files

- fetch policy/client module
- source adapters and extraction service
- pricing worker integration
- OpenAI extraction schemas
- fixture pages and deterministic tests
- configuration, metrics, README, and Unraid notes

### Individual Implementation Instructions

1. Implement one adapter per approved source shape. Prefer APIs, feeds, JSON-LD, or downloadable
   price books before HTML scraping; do not create a universal selector soup.
2. Enforce robots policy, a descriptive user agent, per-domain concurrency/delay, response size and
   content-type limits, redirect limits, timeouts, and SSRF checks on every hop.
3. Extract only relevant local text/metadata, clearly delimit it as untrusted data, and send it to
   OpenAI with a strict evidence schema.
4. Require exact product/release/edition/size, evidence type, amount, currency, sale status/date,
   source URL, observed date, confidence, and a short supporting basis.
5. Reject impossible/out-of-range values, mismatched products, missing provenance, retailer asking
   prices mislabeled as MSRP or secondary sales, and conflicting currency/size data.
6. Upsert observations idempotently using a source/evidence fingerprint. Preserve corrected history
   rather than mutating old observations in place.
7. Calculate displayed recommendations in application code from eligible fresh observations; use
   the model for extraction/classification, not arithmetic authority.
8. Add source health, fetch duration, parse result, accepted/rejected evidence, and OpenAI usage
   telemetry with bounded labels.

### Completion Evidence

- Fixture-driven tests cover every adapter, hostile page instructions, malformed values, retries,
  deduplication, source blocks, and provenance.
- No deterministic test accesses the internet or OpenAI.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A10 tracker row is updated.

---

## A11 — Add Qdrant Indexing and Ollama-First Price Retrieval

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation. Reuse
the proven A03/A04 infrastructure instead of creating a parallel Qdrant path.

### Goal

Index accepted evidence in Qdrant and use local retrieval plus Ollama before calling the existing
OpenAI grounded-price fallback.

### Dependencies

A06 and A10.

### Expected Files

- Qdrant client/indexing module
- Ollama embedding and grounded-synthesis additions
- pricing orchestration changes
- configuration/admin fields
- tests with fake Ollama/Qdrant/OpenAI clients
- `.env.example`, Compose smoke topology, README/Unraid notes

### Individual Implementation Instructions

1. Add Qdrant URL, collection, timeout, and embedding-model settings without exposing Qdrant
   publicly. Validate configuration and show it in managed admin settings.
2. Generate embeddings through Ollama's embedding endpoint using one configured model for indexing
   and querying. Store the embedding model/version with index state.
3. Create a collection whose points reference stable SQL observation/product IDs. Payloads include
   product identity, evidence type, source ID, date, currency, bottle size, and block/eligibility
   metadata needed for filters.
4. Make indexing idempotent and repairable from SQL. A Qdrant outage must not roll back accepted SQL
   evidence or make the catalog unusable.
5. On refresh, perform exact SQL identity/filtering first, retrieve semantically relevant eligible
   context from Qdrant second, and ask Ollama for schema-constrained synthesis third.
6. Validate Ollama output against the retrieved SQL evidence. Ollama may explain or choose among
   evidence but may not invent a price or source.
7. Call OpenAI grounded web search only when local evidence is missing, stale, conflicting, or below
   confidence thresholds. Feed accepted fallback evidence back through the normal SQL/index path.
8. Expose which tier supplied the result (`local`, `ollama_grounded`, `openai_fallback`) and record
   latency, failure, and avoided-fallback metrics.

### Completion Evidence

- Tests cover exact identity filtering, blocked sources, stale evidence, Qdrant outage, Ollama
  hallucination rejection, and OpenAI fallback thresholds.
- Docker/Unraid networking keeps Qdrant internal.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A11 tracker row is updated.

---

## A12 — Add User-Authorized Manual and Browser-Assisted Imports

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation.

### Goal

Allow evidence from approved sources requiring a physical user or authenticated browser without
storing site credentials in Bourbon Book or circumventing access controls.

### Dependencies

A07, A10, and A11.

### Expected Files

- manual-import models and migration
- admin/user import routes and templates
- import parser/extraction integration
- optional local Playwright helper kept outside the production web process
- security, expiry, and import tests
- operator/user documentation

### Individual Implementation Instructions

1. Mark sources with `manual` access mode so scheduled cycles create `waiting_for_user` work instead
   of repeatedly failing automated fetches.
2. Implement the MVP import form for an authorized user to submit a URL plus copied text, saved HTML,
   PDF, screenshot, or another explicitly supported artifact. Enforce tight type/size limits.
3. Create short-lived, single-use import sessions bound to user, source, expected domain, and pricing
   job. Hash tokens at rest and expire/revoke them on use.
4. Pass imported material through the same OpenAI extraction, validation, SQL persistence, Qdrant
   indexing, and source-preference rules as automatic fetches.
5. Add a local browser-assisted helper using Playwright only after the manual upload path is secure.
   It opens the approved URL in a user-controlled session, waits for the user to authenticate and
   confirm capture, extracts the rendered relevant content, and submits it with the one-time token.
6. Do not transmit or persist browser cookies, passwords, local storage, unrelated page content, or
   hidden credential fields. Never automate CAPTCHA or bypass access restrictions.
7. Document that Codex/Playwright MCP may exercise this flow interactively during development, but
   it is not the production service boundary.

### Completion Evidence

- Tests cover token binding/expiry/reuse, authorization, malicious files/content, source mismatch,
  blocked sources, and normal ingestion.
- A manual browser-assisted smoke test is documented without exposing credentials or page content.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A12 tracker row is updated.

---

## A13 — Complete End-to-End Evaluation and Unraid Operations

### Status Gate

Deferred pending the post-RAG design checkpoint; rewrite this action before implementation.

### Goal

Measure the finished local-first pricing pipeline, document production operation, and prove safe
deployment and rollback on Unraid.

### Dependencies

A01–A12.

### Expected Files

- evaluation scripts/tests and reports
- README and deployment runbook
- `.env.example`, Compose smoke topology, Docker health/readiness behavior
- metrics/dashboard guidance
- final plan status updates

### Individual Implementation Instructions

1. Run the reconciled image/product suite through identity matching and the pricing evidence suite
   through local SQL, Qdrant/Ollama, and OpenAI fallback paths.
2. Report coverage, exact-release accuracy, accepted-price error/tolerance, stale-evidence behavior,
   source diversity, latency, fallback rate, and avoided OpenAI calls. Do not silently update
   baselines to make results pass.
3. Add end-to-end fake-provider tests for manual price edit → prioritized job → source refresh →
   automatic replacement → retained history, plus user blacklist and administrator source approval.
4. Validate restart recovery, expired job leases, Qdrant downtime/reindex, OpenAI downtime, Ollama
   downtime, source failures, and rollback behavior.
5. Document Unraid settings for Qdrant URL/collection, embedding model, refresh cadence, OpenAI
   discovery/extraction/fallback, volumes, internal networks, health checks, logs, and backups.
6. Add a rollout checklist: back up `/data`, deploy migrations, verify readiness, seed/approve
   sources, run a small refresh, inspect evidence/provenance, test manual import, verify metrics/logs,
   and retain a rollback image/data snapshot.
7. Run `make pr-review` and an explicit production-image/container smoke test with fake or disabled
   external providers.

### Completion Evidence

- Evaluation results and known limitations are documented.
- Deployment, health, backup, restore, and rollback runbooks are complete.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A13 tracker row is updated.

## Plan-Review Prompt

Use this prompt in a fresh context window before implementation:

```text
Review the Bourbon Book implementation plan at <absolute path>/plan.md, focusing on the new
RAG-first sequence in A03 and A04. Verify that the plan cleanly proves Ollama embeddings, Qdrant
configuration, fixture loading, metadata filtering, and raw vector search without prematurely
choosing production price sources, schemas, or recommendation behavior. Check dependencies,
interfaces, failure handling, tests, Docker/Unraid operations, and the post-RAG checkpoint. Do not
implement the plan or reactivate A05–A13 during this review.
```

## Next-Session Implementation Prompt Template

Use this template after completing an action and pushing its plan-status update:

```text
Continue the Bourbon Book implementation plan at <absolute path>/plan.md.

Take only action <ID — title> in a new branch named <branch>. Verify that all dependency PRs listed
for the action have merged into the remote default branch before editing. Follow the action's
individual instructions and the Required Lifecycle exactly: focused tests, independent sub-agent
validation, resolve findings, make pr-review, scoped commit/push, draft PR, then mark the action
Complete in plan.md and push that status update. Preserve unrelated working-tree changes. After the
PR and status update succeed, create a new Codex session for the next Incomplete action.
```

## Current Work

A01 and A02 are complete and merged through PRs #11 and #12. Physical iPhone Safari/PWA picker and
HEIC behavior remain an explicit device acceptance check because desktop automation cannot
faithfully emulate the native iOS picker.

Follow-up commit `dbfe20d` broadened Atkinson Hyperlegible Next to the remaining form-value,
search, sort, price, and quantity controls, standardized readable value text at `1.05rem`/`1.45`,
and made local account verification easier by capturing email and displaying the verification link
outside production. Production remains the only environment that sends through SMTP.

The next implementation action is A03 on `codex/rag-infrastructure`. A04 follows only after A03 is
merged. After A04, stop for the post-RAG design checkpoint; A05–A13 remain deferred until their
production data, evidence, and recommendation assumptions are reviewed and rewritten.
