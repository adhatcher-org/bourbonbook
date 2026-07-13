# Phase 1 Implementation Plan: GTX 1070 Ti

## Handoff contract

This is the coding plan for a GPT-5.4-mini implementation agent. Its goal is to improve local,
OpenAI-independent bottle analysis on an 8 GB GTX 1070 Ti without accepting any regression in
accuracy, reliability, or measured latency.

Implement exactly one numbered action per branch and session. Before starting, add the action to
the `plan.md` tracker and follow its required lifecycle, including its commit-bound review and
validator sequence. The current worktree contains uncommitted P1-01 benchmark tooling:

- `bourbonbook/benchmark_cli.py`
- `docs/ai-performance-plan.md`
- `tests/test_benchmark_cli.py`

Treat those files as in-scope only for P1-01. Preserve every unrelated user change.

## Non-negotiable benchmark gate

### Ground truth and baseline

The owner-approved 24-bottle Unraid collection is the photo/name ground truth. Export it only using
`python -m bourbonbook.benchmark_cli export` for one exact owner ID or username. The fixture is
private app data, never a committed test fixture. It may contain copied photos, anonymous case IDs,
image hashes, and analysis fields only; it must exclude account identifiers, notes, storage
locations, purchase price, price sources, URLs, credentials, and provider payloads. MSRP is
reference-only because a photograph cannot prove a current price.

Before the first inference change, deploy P1-01 and create `/data/benchmarks/fixtures/collection-v1`.
Record the current app image/commit, provider, model, Ollama/runtime version, GPU, fixture digest,
and timestamp outside Git. Deliberately unload/restart the local model, then run photo and name
operations with three warm trials per case and `--cold-start-state unloaded`. Save that private
report as the approved baseline.

Live benchmarks are opt-in operational runs. They may use local GPU resources or, if OpenAI is
active, send photos externally and incur cost. Deterministic tests must never call a live provider.

### Candidate acceptance

Run the identical fixture, operations, run count, and cold-start state after one bounded change.
`benchmark_cli compare` must pass. That means:

- matching fixture digest and run count;
- a completed, no-slower cold-start request when both reports are `unloaded`;
- no worse warm p50/p95 for every operation;
- no lower provider success count; and
- no lower coverage or accuracy for `name`, `brand`, `proof`, `abv`, `size`, `status`, or
  `fill_level`.

A speed gain with lower accuracy, or an accuracy gain with slower user-visible latency, fails.
Retain rejected reports privately; never enable their production configuration.

## P1-01 — Benchmark foundation and baseline capture

**Goal:** land and harden the private benchmark tool before any inference change.

**Primary paths:** `bourbonbook/benchmark_cli.py`, `tests/test_benchmark_cli.py`,
`docs/ai-performance-plan.md`, this plan, and README only for a short runbook link.

**Implementation requirements:**

1. Keep the tool module/CLI-only: no web, admin, or API route.
2. Restrict fixture/report paths to resolved `DATA_DIR/benchmarks`; reject traversal, symlink escape,
   unsafe/missing photos, missing owners, and a non-empty destination.
3. Query only the explicitly selected owner's bottles; exclude shopping-list items; fail rather than
   silently reducing the cohort when an eligible bottle has no safe photo.
4. Copy source photos to a 0700 fixture directory with 0600 files. Use anonymous IDs and verify
   manifest/photo SHA-256 hashes before a run.
5. Record one labelled cold-start sample plus independent warm samples. Report external wall-clock
   duration, provider/model, success count, field comparisons, coverage, p50/p95, and fixture hash.
   Never write prompts, responses, user details, or image contents to reports/logs.
6. Make comparison fail on fixture/run/cold-state mismatch, cold or warm regression, lower success,
   or lower critical-field coverage/accuracy.
7. Do not score MSRP/current price from a photograph.

**Deterministic tests:** owner scope; shopping-list exclusion; source DB unchanged; missing/unsafe,
traversal, and symlink photos; fixture privacy fields; copied-file hashes; malformed manifest; fake
provider success/partial/malformed/timeout; cold/warm report shape; every comparator failure mode;
and private output-path enforcement.

**Operational completion evidence:** an approved private baseline report from all 24 bottles. Do not
commit the fixture or report.

## P1-02 — 1070 Ti Ollama topology and model selection

**Goal:** deploy local GPU inference and select a model only through the baseline gate.

**Scope:** Unraid/Docker configuration and documentation; no request-orchestration change.

1. Deploy Ollama with NVIDIA passthrough, persistent model storage, internal-only networking, and no
   public port. Set `OLLAMA_URL` to the internal service; do not put secrets in docs/compose.
2. Keep one analysis request at a time and context at 4096 tokens or lower on the 8 GB card.
3. Benchmark `qwen2.5vl:3b` and `gemma3:4b` separately from the same unloaded state/fixture/runs.
   Record GPU/runtime evidence privately.
4. Change `OLLAMA_MODEL` only after a passing comparison; otherwise retain the current model.

**Completion evidence:** GPU-use/model-persistence proof and a passing private candidate report.

## P1-03 — Reusable provider-client lifecycle

**Goal:** remove per-request connection setup without changing provider semantics.

**Likely paths:** `bourbonbook/main.py`, `bourbonbook/ollama.py`,
`bourbonbook/openai_provider.py`, `bourbonbook/analysis.py`, observability/provider tests.

1. Create reusable clients/transports in the FastAPI lifespan and close them at shutdown. Never put
   request context or credentials in mutable module globals.
2. Preserve provider contracts, normalized statuses, usage records, bounded error mapping, and the
   current 120-second timeouts unless a separate benchmarked action changes them.
3. Keep OpenAI and Ollama isolated; a client change in one cannot alter the other fallback path.
4. Use injected fakes to prove reuse, closure, timeout, malformed output, and provider-error paths.

**Acceptance:** focused deterministic tests and a passing live candidate comparison.

## P1-04 — Release database sessions during inference

**Goal:** prevent slow analysis from holding a SQLite session/transaction open.

**Likely paths:** `bourbonbook/main.py`, `tests/test_app.py`, runtime-boundary and provider tests.

1. Authenticate/authorize and capture only safe identifiers/photo paths in an open session, then
   close it before every provider `await`.
2. Reopen a session after inference, re-check ownership, and apply to only the same bottle. Preserve
   CSRF, redirects, failure banners, and existing manual values.
3. Cover add, replace/re-analyze, name analysis, provider failure, deletion/reassignment during
   inference, and concurrent user edit behavior. Do not introduce queues/background jobs here.

**Acceptance:** tests prove no session spans inference and the candidate report passes.

## P1-05 — Deterministic image-input preparation

**Skills required:** `provider-evaluation` and `pwa-visual-check`.

**Goal:** reduce oversized image transfer/inference cost without replacing the original upload.

**Likely paths:** `bourbonbook/photos.py`, both provider modules, config/admin config,
`.env.example`, README, image tests, and upload visual tests.

1. Add one reusable analysis-image preparation function: EXIF correction, documented edge/pixel
   limits, JPEG normalization, and bounded quality. Retain original uploads untouched.
2. Make limits explicit managed configuration; include a preprocessing revision in benchmark metadata
   and all future cache keys.
3. Preserve existing malformed/decompression-bomb/format safety behavior. Do not add OCR, object
   detection, or a UI redesign.

**Acceptance:** image safety/format tests, browser upload verification, and a passing comparison.

## P1-06 — Provider-neutral validation and explicit fallback

**Goal:** reject invalid local output and permit an auditable, opt-in OpenAI analysis fallback.

**Likely paths:** analysis/provider/config/admin-config/observability modules, tests,
`.env.example`, and README.

1. Define one provider-neutral result schema. Validate field types/ranges, proof-to-ABV consistency,
   fill/status consistency, and allowed status values before persistence.
2. Classify malformed, partial, or ambiguous results as unavailable/invalid; keep manual edit usable.
3. Enable OpenAI fallback only through explicit configuration and only after local analysis is invalid
   or unavailable. Record bounded fallback metadata. Never call OpenAI after a valid local result.
4. Keep price search out of this path.

**Acceptance:** fake-provider cases for valid/partial/malformed/timeout/fallback disabled/enabled/no
unnecessary OpenAI; a passing local-primary benchmark; separate fallback evidence for invalid cases.

## P1-07 — Validated analysis cache

**Skills required:** `migration-change` and `provider-evaluation`.

**Prerequisite:** the benchmark runner must disable cache or clearly attribute cache hits. Model
comparisons are always uncached.

1. Add a forward-only private cache keyed by image SHA-256, provider, model, prompt revision, schema
   revision, and preprocessing revision. Cache validated results only.
2. Enforce owner isolation; store no raw image, prompt, or provider payload. Define TTL,
   invalidation, hit/miss metrics, and failure fallback to normal analysis.
3. Test new/upgraded databases, hit/miss, revision invalidation, ownership isolation, malformed-result
   non-caching, and fallback behavior.

**Acceptance:** uncached candidate comparison passes; private cache-hit evidence shows the expected
repeat-request improvement; migration and full project validation pass.

## Explicit deferrals

Do not include in Phase 1: models larger than the 1070 Ti tier (`qwen2.5vl:7b`, `qwen3-vl:8b`,
Gemma 3 12B); multi-model residency/concurrency; vLLM/NIM; durable jobs/progress UI; OCR/catalog
grounding (reserved for A03/A04 and their design checkpoint); or pricing retrieval/evidence. The RTX
3090 phase starts by rerunning the exact approved fixture and comparator unchanged.

## Required validation

Run focused tests while iterating. Before every PR, run `make pr-review`, then follow the exact
candidate-commit review/validator and draft-PR workflow in `AGENTS.md`. A fix creates a new candidate
commit and requires fresh commit-bound review and validation.
