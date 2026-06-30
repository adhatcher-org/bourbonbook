# Bourbon Book TODO Implementation Plan

## Purpose

This plan implements the open work in `docs/todo.md` as a sequence of independently reviewable
actions. Only one action is implemented in a branch and Codex session at a time. Every action must
finish with tests, an independent sub-agent review, `make pr-review`, a pushed branch, and a draft
pull request before its status changes from **Incomplete** to **Complete**.

The pricing work is intentionally pricing-evidence-first. Structured SQL records are the source of
truth for prices and provenance. Qdrant provides semantic retrieval, while Ollama consumes retrieved
context before Bourbon Book falls back to OpenAI.

## Confirmed Decisions

1. Removing the HTML `capture="environment"` hint will restore the normal iPhone image chooser;
   actual camera, Photo Library, PWA, and HEIC behavior must be verified.
2. Edit-form values will use self-hosted **Atkinson Hyperlegible Next**. Existing control sizes and
   button typography remain unchanged.
3. Pricing evidence is the initial ingestion scope. Ratings, reviews, and broader product content
   are deferred until the pricing pipeline is working and measured.
4. SQL is authoritative for exact product identities, prices, source records, dates, and history.
   Qdrant stores embeddings and searchable summaries keyed back to SQL records.
5. A manual MSRP or suggested secondary-price edit creates a durable, prioritized refresh job.
6. A successful source-backed refresh automatically replaces the displayed manual price. The
   superseded manual value remains in price history with `user_reported` provenance.
7. Users may immediately blacklist a source for their own results and may suggest a new source.
   Only an administrator may activate, disable, or globally blacklist crawl sources.
8. OpenAI proposes candidate sites and extracts structured evidence. Model-proposed sites require
   administrator approval before the server accesses them.
9. Sources requiring an authenticated human session use an explicit manual-import workflow. The
   system must not bypass a paywall, firewall, access control, or site terms.
10. The existing `tests/images` set will be reused for evaluation after its missing and unvalidated
    fixtures are reconciled. Pricing expectations will gain source and `as_of` metadata.
11. Bourbon Book continues to run as one Uvicorn worker in Docker on Unraid. The durable pricing
    worker runs in that process until the application adopts a database suited to multiple writers.

## Action Tracker

| ID | Action | Status | Branch | Pull request / completion evidence |
| --- | --- | --- | --- | --- |
| A01 | Restore iPhone Photo Library selection | In Progress | `codex/iphone-photo-picker` | Implementation and validation underway. |
| A02 | Apply Atkinson Hyperlegible Next to edit controls | Incomplete | `codex/readable-edit-font` | — |
| A03 | Reconcile and extend the pricing evaluation fixtures | Incomplete | `codex/pricing-evaluation-fixtures` | — |
| A04 | Add the pricing evidence schema and separate provider roles | Incomplete | `codex/pricing-evidence-foundation` | — |
| A05 | Add governed source administration and user preferences | Incomplete | `codex/pricing-source-registry` | — |
| A06 | Add durable refresh jobs and automatic manual-price replacement | Incomplete | `codex/pricing-refresh-jobs` | — |
| A07 | Add OpenAI-assisted source discovery | Incomplete | `codex/pricing-source-discovery` | — |
| A08 | Add scheduled fetching and OpenAI evidence extraction | Incomplete | `codex/pricing-evidence-ingestion` | — |
| A09 | Add Qdrant indexing and Ollama-first price retrieval | Incomplete | `codex/qdrant-ollama-pricing` | — |
| A10 | Add user-authorized manual and browser-assisted imports | Incomplete | `codex/manual-source-import` | — |
| A11 | Complete end-to-end evaluation and Unraid operations | Incomplete | `codex/pricing-pipeline-validation` | — |

## Required Lifecycle for Every Action

Each action must use this lifecycle without combining the next action into the same branch:

1. Start from the current remote default branch after all required predecessor PRs have merged.
2. Confirm `git status --short`; preserve and do not stage unrelated user work.
3. Create the branch named in the tracker. If it already exists, inspect it before reusing it.
4. Change the tracker row to `In Progress` locally while working. Do not call the action complete yet.
5. Implement only the named action and its documentation/tests. Prefer extending existing modules
   over adding parallel implementations.
6. Run focused tests throughout development, followed by the relevant full local checks.
7. Spawn a sub-agent with instructions to inspect the final diff, run or inspect focused tests, and
   report correctness, regressions, missing coverage, security concerns, and scope creep. The
   reviewer must not modify files unless explicitly asked after its report.
8. Resolve every actionable sub-agent finding and rerun affected tests.
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
- Keep SQL and Qdrant records linked by stable SQL IDs. SQL must remain usable if Qdrant is down.
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

Improve edit-field readability by changing the typeface without increasing control sizes or
changing button typography.

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
4. Apply the family to `.field-grid input`, `.field-grid select`, and `.field-grid textarea`. Include
   any edit-only value control currently outside `.field-grid`; do not change labels or buttons.
5. Preserve current font sizes, spacing, colors, focus styles, and mobile layout.
6. Verify representative text, decimal values, punctuation, placeholders, selects, and multiline
   notes on iPhone and desktop. Check that missing-font fallback remains readable.

### Completion Evidence

- Font assets and license are tracked and served by the application.
- Visual verification is documented with desktop and iPhone screenshots.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A02 tracker row is updated.

---

## A03 — Reconcile and Extend the Pricing Evaluation Fixtures

### Goal

Turn the existing bottle-image expectations into a stable identity-and-pricing evaluation baseline
before changing pricing behavior.

### Dependencies

None technically; complete before A08 and A09.

### Expected Files

- `tests/images/ImageTestValidation.md`
- `tests/images/` fixtures
- `scripts/evaluate_ollama.py`
- `tests/test_evaluation.py`
- new pricing-evaluation fixture/schema if separation makes the tests clearer

### Individual Implementation Instructions

1. Reconcile the current inventory: `WellerSpecialReserve.jpeg` is referenced but absent, while
   `BuffaloTrace.jpeg` and `EHTaylorSmallBatch.jpeg` currently lack expected records. Preserve user
   assets and ask for the missing image if it cannot be recovered from repository history.
2. Separate image-derived identity expectations from pricing expectations. A photograph must not be
   treated as evidence of a current price.
3. Add `as_of`, currency, bottle size, evidence type, source identity, and an explicit tolerance to
   every pricing expectation.
4. Extend the evaluation parser and tests so missing images, unvalidated images, stale expectations,
   exact-release mismatches, and prices outside tolerance are reported separately.
5. Preserve the existing Ollama identity evaluation and add a provider-independent pricing report
   that can consume fake/local evidence during CI.
6. Document how a maintainer refreshes baselines without silently accepting new model output.

### Completion Evidence

- Every present test image is intentionally validated or explicitly excluded.
- Pricing baselines have provenance and dates.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A03 tracker row is updated.

---

## A04 — Add the Pricing Evidence Schema and Separate Provider Roles

### Goal

Establish durable product, evidence, and job primitives while decoupling image analysis, local price
retrieval, and OpenAI fallback.

### Dependencies

A03 should be merged so the architecture has a stable evaluation target.

### Expected Files

- `bourbonbook/models.py`
- new Alembic migration
- `bourbonbook/config.py`
- `bourbonbook/admin_config.py`
- `bourbonbook/analysis.py`
- focused models/config/migration tests
- `.env.example`, `README.md`

### Individual Implementation Instructions

1. Add a canonical catalog-product table with normalized identity fields and aliases. Link bottles
   to it with a nullable foreign key so existing records migrate safely.
2. Add immutable price-observation records containing product, bottle size, evidence type, amount,
   currency, source, source URL, observed/sale date, extraction method, confidence, checked time,
   and optional owning user for private `user_reported` evidence.
3. Add a durable refresh-job table with product/bottle identity generation, priority, reason,
   pending/running/completed/failed state, attempts, lease timestamps, and bounded failure type.
4. Preserve existing `PriceSource` behavior during migration or provide an explicit data migration;
   do not drop historical URLs or displayed prices.
5. Replace the single `ANALYSIS_PROVIDER` decision with explicit roles: identity analyzer, local
   pricing enabled state, and OpenAI fallback enabled state. Maintain backward-compatible defaults.
6. Add indexes and uniqueness constraints needed for idempotent evidence ingestion and one active
   refresh request per product/generation.
7. Test fresh creation, recognized legacy migration, settings validation, relationships, deletion,
   and idempotent upserts.

### Completion Evidence

- Fresh and legacy-shaped database migration tests pass.
- Existing bottle creation and price display remain functional.
- Sub-agent review and `make pr-review` pass.
- Draft PR exists and the A04 tracker row is updated.

---

## A05 — Add Governed Source Administration and User Preferences

### Goal

Let administrators govern crawl sources while users can blacklist sources and submit candidates.

### Dependencies

A04.

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
- Draft PR exists and the A05 tracker row is updated.

---

## A06 — Add Durable Refresh Jobs and Automatic Manual-Price Replacement

### Goal

Queue prioritized pricing refreshes when a user edits MSRP or suggested secondary price, and safely
replace displayed manual prices when fresh grounded evidence becomes available.

### Dependencies

A04 and A05.

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
- Draft PR exists and the A06 tracker row is updated.

---

## A07 — Add OpenAI-Assisted Source Discovery

### Goal

Use OpenAI web search to propose useful pricing domains without allowing model output to become an
automatic crawl instruction.

### Dependencies

A05 and A06.

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
- Draft PR exists and the A07 tracker row is updated.

---

## A08 — Add Scheduled Fetching and OpenAI Evidence Extraction

### Goal

Fetch approved public sources conservatively and use OpenAI to convert relevant page data into
validated, idempotent pricing observations.

### Dependencies

A05–A07.

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
- Draft PR exists and the A08 tracker row is updated.

---

## A09 — Add Qdrant Indexing and Ollama-First Price Retrieval

### Goal

Index accepted evidence in Qdrant and use local retrieval plus Ollama before calling the existing
OpenAI grounded-price fallback.

### Dependencies

A04 and A08.

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
- Draft PR exists and the A09 tracker row is updated.

---

## A10 — Add User-Authorized Manual and Browser-Assisted Imports

### Goal

Allow evidence from approved sources requiring a physical user or authenticated browser without
storing site credentials in Bourbon Book or circumventing access controls.

### Dependencies

A05, A08, and A09.

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
- Draft PR exists and the A10 tracker row is updated.

---

## A11 — Complete End-to-End Evaluation and Unraid Operations

### Goal

Measure the finished local-first pricing pipeline, document production operation, and prove safe
deployment and rollback on Unraid.

### Dependencies

A01–A10.

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
- Draft PR exists and the A11 tracker row is updated.

## Next-Session Prompt Template

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

A01 is active on `codex/iphone-photo-picker`. GitHub authentication was verified before branch
creation. The unrelated local `.env.example` edit and untracked `docs/` directory are explicitly
outside this action and must not be staged.
