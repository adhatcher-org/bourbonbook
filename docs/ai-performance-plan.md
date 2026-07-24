# AI Performance and Accuracy Plan

This plan is a separate, evidence-first workstream alongside the [RAG roadmap](adr/plan.md).
It does not change production analysis settings without a candidate benchmark report that is at
least as accurate and as fast as the approved baseline.

The coding handoff is in [Phase 1 implementation plan](phase-1-1070ti-implementation-plan.md).

## Benchmark gate

1. Use the existing owner-approved collection to create a private fixture snapshot.  The snapshot
   includes copied photos, image hashes, and only the fields relevant to bottle analysis.  It never
   contains an owner ID, username, email, notes, storage location, purchase price, price sources,
   or credentials.
2. Before changing inference, deliberately unload/restart the selected local model, then run the
   current production provider/model on the frozen fixture with three warm trials per case. Save the
   first-request cold-start sample and the warm report as the approved baseline.
3. Repeat the exact benchmark after each single bounded change.
4. Accept a candidate only when its controlled cold-start sample and every operation's warm p50 and
   p95 wall-clock latency are no slower, provider success count is no lower, and every critical
   field has no lower accuracy or coverage than baseline.
   Critical fields are name, brand, proof, ABV, size, status, and fill level.
5. Keep deterministic fixture/unit tests in CI.  Live model reports are operational evidence and
   must be run deliberately because they may use local GPU resources or send photos to OpenAI.

Photo analysis does not score MSRP: the product prompt intentionally returns `null` for MSRP and a
photo cannot establish a current price.  Exported MSRP is reference-only.  Price retrieval will need
a separate source-backed, dated benchmark after the pricing roadmap is implemented.

## Phase 1: GTX 1070 Ti (8 GB)

1. Capture and approve the current production baseline.
2. Deploy internal-only GPU-backed Ollama with persistent model storage and confirm GPU use.
3. Evaluate `qwen2.5vl:3b` and `gemma3:4b` with the same fixture.  Keep one request at a time and
   a 4K-or-lower context window.
4. Add provider-neutral improvements one at a time: long-lived client reuse, bounded image
   preprocessing, strict structured-output validation, cache-by-image/model/prompt revision, and
   DB-session separation from inference.
5. Use the existing A03/A04 RAG work for catalog grounding.  OCR/retrieval should identify likely
   bottles before a VLM verifies bottle-specific details.
6. Keep OpenAI as a measured fallback for invalid or ambiguous local results.  Price research remains
   a separate source-backed operation.

## Phase 2: RTX 3090 (24 GB)

The 3090 creates enough headroom to use larger specialist models, but it does not make multiple
large models resident for one user transaction a safe default. Preserve the benchmark gate: no model
below is a production selection until it has passed the frozen collection benchmark.

### Model roles

| Application work | Primary path | Local model role | Non-LLM evidence and guardrails |
| --- | --- | --- | --- |
| Add a bottle from a photo: label transcription, identity, bottle-specific visual facts, status, and fill level | Photo-analysis job | `gemma4:26b` is the primary VLM candidate | Normalize the image first; retain extracted label text and confidence. OCR text cannot establish fill level, so preserve its separate visual result and request user confirmation when confidence is low. |
| Add a bottle from a photo: durable product attributes | Local catalog after the photo job | No routine LLM call | Match the recognized name/label text to the versioned catalog to supply distillery, mash bill, standard proof/ABV, and size. Do not let the VLM invent missing product facts. |
| Resolve an unknown or ambiguous recognized bottle | Exception path after catalog matching fails | `qwen3:30b-a3b` for fast text/name reconciliation | Send the existing extracted evidence only; do not make this a second call for every successful photo. Persist an unresolved result for user correction rather than guessing. |
| Look up or refresh bottle attributes from a typed bottle name | Catalog-first name lookup | `qwen3:30b-a3b` only for a catalog miss or ambiguity | Exact catalog matches return immediately. The general model may reconcile a name, then the catalog remains the authority for durable fields. |
| Add or refresh MSRP | Source-backed pricing job | No LLM | Use the existing local OHLQ cache and a direct OHLQ/imported catalog source with exact product-and-size matching, source URL, and checked date. A cache miss may be unavailable; no model may infer MSRP. |
| Continue-assisted development | Developer workstation, never the application request path | `qwen3-coder:30b` | Unload it before application benchmarks or user analysis so it cannot consume VRAM or bias results. |

`qwen3.6:35b` is a benchmark candidate for broader text reconciliation only; it is not the default
until it passes the same name-lookup accuracy and latency gate. `qwen3-vl:8b` is not a production
default: its prior fixture report regressed photo latency and critical extraction accuracy against the
OpenAI baseline. It may be retained only as a control in future benchmark reports.

### User-transaction timing and model residency

1. Re-run the unchanged private fixture on the 3090 after confirming GPU passthrough, sustained
   thermals, model digest, and model persistence. Treat any 1070 Ti report as historical comparison
   evidence only; capture every new cold-start and warm sample on the 3090. Record `nvidia-smi`,
   `ollama ps`, Ollama version, and whether the selected model was unloaded for the cold-start sample.
2. Benchmark `gemma4:26b` as the photo candidate. Benchmark `qwen3:30b-a3b` and `qwen3.6:35b` only
   on the name/reconciliation operation. Select each role independently through the benchmark gate;
   do not compare text-token throughput with end-to-end photo completion time.
3. Treat the normal photo transaction as one VLM call followed by local catalog work. Show progress
   while the photo job is running, then save the result for review. A catalog miss may enqueue the
   text-reconciliation exception, but the user-facing flow must identify that additional stage rather
   than silently loading another large model.
4. Treat the normal typed-name transaction as catalog-first. Only load/use the general model after a
   miss or ambiguity. This keeps common name edits fast and avoids loading the vision model.
5. Treat pricing as a separate source-backed refresh. Apply a fresh matching cached price instantly;
   otherwise queue or run the OHLQ/import job and show the timestamp/source. It must never block a
   photo or name-analysis model call.
6. Keep exactly one large application model resident initially. A photo request uses the VLM; a
   reconciliation/name request uses the general model; Continue's coding model is unloaded outside
   development. Measure model-load/eviction time separately from inference and include it in the
   user-visible cold-start report. Do not preload both the 26B vision model and 30B general/coding
   models on a 24 GB card.
7. Add a bounded GPU job queue and limited concurrency only after measuring VRAM under simultaneous
   analysis. Record queue wait, model-load/eviction, inference, catalog, and pricing times separately.
   Move jobs to durable background execution with progress UI and user confirmation for low-confidence
   fields only after the end-to-end user-visible completion benchmark passes.
8. If concurrent requests justify it, perform a separate vLLM evaluation. Retain Ollama unless the
   same role-specific benchmark proves a vLLM candidate meets the gate.

### Changes from original Phase 2 plan

- Replaced the generic `qwen2.5vl:7b` / `qwen3-vl:8b` / Gemma 3 12B shortlist with measured-role
  candidates: `gemma4:26b` for photos, `qwen3:30b-a3b` for text reconciliation, and
  `qwen3.6:35b` as an experimental text candidate.
- Explicitly removed `qwen3-vl:8b` as the production default because its recorded benchmark regressed
  both latency and critical photo accuracy.
- Added a catalog-first, source-backed transaction design so durable attributes and MSRP are not
  invented by an LLM; MSRP is now explicitly a non-LLM OHLQ/cache operation.
- Added the one-large-model residency rule, explicit model-load/eviction timing, and visible exception
  stages to account for the 3090's 24 GB VRAM during real user transactions.

### Current implementation audit (2026-07-21)

The committed Phase 2 foundation on `feature/phase2-localllm` is useful, but it is not an accepted
local-only cutover. The audit deliberately excludes uncommitted work in `catalog_extract.py`,
`bourbonbook/migrations/`, `tests/tmp/`, and `.vscode/`.

| Area | Current state | Remaining boundary |
| --- | --- | --- |
| Model configuration and basic local analysis | Partial | `OLLAMA_VISION_MODEL` / `OLLAMA_TEXT_MODEL`, catalog matching, and local extraction exist, but defaults and model acceptance have not been proven on the 3090. |
| Benchmarking | Incomplete | The v1 report counts only `complete` as success, scores non-observable fields for name operations, has permissive name matching, and records no trustworthy runtime/residency evidence or local-only boundary. |
| Photo/catalog workflow | Partial | The request path performs local extraction and catalog matching, but is synchronous and lacks a confidence contract, durable jobs, bounded queue, timings, progress, and confirmation workflow. |
| Pricing | Partial | SQLite/Qdrant catalog-price foundations and an import CLI exist, but matching is fuzzy and a cache miss can still invoke OpenAI web search. Automatic MSRP records are not yet governed by exact identity and source provenance. |
| OpenAI removal | Incomplete | The OpenAI provider, client lifecycle, settings, admin/environment/docs references, dependency, and runtime price path remain. |

### Required sequential implementation handoff

Do not combine the following actions. Each implementation agent owns only its named action; a fresh,
independent validation/fix agent follows it. The next implementation agent starts only after the
preceding validation agent reports passing focused tests and the full test suite. Each action must
add focused regression coverage of at least **80%**. The repository's existing **90%
branch-coverage** `make coverage` threshold remains mandatory before a branch can be promoted to a
pull request; an aggregate shortfall must be reported explicitly and never attributed to an
unrelated action.
Deterministic tests must use fakes and captured fixtures; live Ollama, GPU, Qdrant, OpenAI, and web
calls are prohibited in tests.

| Order | Action and implementation agent | Independent validation/fix agent | Required implementation and acceptance evidence |
| --- | --- | --- | --- |
| 1 | **P2-00 — `p2_00_benchmark_implementer`**: repair benchmark semantics and evidence contract. | **`p2_00_benchmark_validator`** | Count `complete` and catalog `verified` terminal results as success; score only observable fields per operation; use strict canonical size/unit equivalence and no fuzzy identity success; add a versioned report migration/compatibility policy; capture non-secret Ollama/GPU/model/preprocess/queue/load evidence; add an explicit local-only no-OpenAI guard. Add fixture/fake tests for every rule, with ≥80% focused coverage. |
| 2 | **P2-01 — `p2_01_model_evaluation_implementer`**: add the role-selection runner and acceptance recording. | **`p2_01_model_evaluation_validator`** | After P2-00 passes, make model trials reproducible and role-scoped: `gemma4:26b` for photos; `qwen3:30b-a3b` and `qwen3.6:35b` for name reconciliation only. Test report selection/rejection and configuration without live calls. A private 3090 run requires Aaron's explicit live-run authorization and must be recorded separately from CI. |
| 3 | **P2-02A — `p2_02a_analysis_implementer`**: harden the local evidence-to-catalog analysis contract. | **`p2_02a_analysis_validator`** | Make catalog data authoritative for durable attributes; restrict text-model reconciliation to catalog miss/ambiguity; validate structured evidence/confidence; persist unresolved results instead of guesses. Add provider fakes and regression tests with ≥80% focused coverage. |
| 4 | **P2-02B — `p2_02b_queue_implementer`**: add bounded GPU scheduling and timing telemetry. | **`p2_02b_queue_validator`** | Enforce one large application model resident; record queue wait, load/eviction, inference, catalog, and price durations; prove ordering, cancellation/failure, and telemetry with deterministic tests. Do not add concurrency or preload without accepted P2-01 evidence. |
| 5 | **P2-02C — `p2_02c_jobs_ui_implementer`**: add durable jobs, progress, and low-confidence confirmation. | **`p2_02c_jobs_ui_validator`** | Add forward-only migration coverage for fresh and upgraded databases; authenticated/owner-scoped progress UI/API; visible exception stages; and user confirmation before applying low-confidence visual facts. Include browser/route tests and ≥80% focused coverage. |
| 6 | **P2-03A — `p2_03a_price_contract_implementer`**: establish the source-backed price evaluation contract. | **`p2_03a_price_contract_validator`** | Add a private/captured price fixture and comparator separate from photo/name analysis. Require exact canonical product/release/edition/size identity, source URL/basis, observation date, currency, and freshness. Test unavailable and non-provenanced results. |
| 7 | **P2-03B — `p2_03b_price_source_implementer`**: replace runtime LLM price lookup with direct evidence. | **`p2_03b_price_source_validator`** | Preserve a fresh exact local cache hit; on a miss use only an approved direct OHLQ/import adapter or return unavailable. Remove fuzzy automatic application and prohibit VLM/LLM MSRP inference. Add adapter, provenance, and no-OpenAI-path tests with ≥80% focused coverage. |
| 8 | **P2-04 — `p2_04_openai_removal_implementer`**: remove production OpenAI runtime support. | **`p2_04_openai_removal_validator`** | After P2-02C and P2-03B have passed their gates, remove OpenAI runtime code, configuration, admin controls, dependency, documentation, and tests that preserve fallback behavior. Add a no-call boundary test proving all application operations remain local-only with ≥80% focused coverage. |

### P2-01 offline role-selection record

`python -m bourbonbook.model_evaluation` consumes captured P2-00 v2 JSON reports only; it does not
load settings, contact Ollama, inspect a GPU, or modify production model configuration. Its versioned
configuration records one baseline per operation plus every candidate report, relative to the config
file:

```json
{
  "schema_version": 1,
  "baselines": {
    "photo": "reports/photo-baseline.json",
    "name": "reports/name-baseline.json"
  },
  "candidates": [
    {"model": "gemma4:26b", "role": "photo", "report": "reports/gemma4-photo.json"},
    {"model": "qwen3:30b-a3b", "role": "name", "report": "reports/qwen3-a3b-name.json"},
    {"model": "qwen3.6:35b", "role": "name", "report": "reports/qwen3-6-name.json"}
  ]
}
```

The runner records each expected candidate as `accepted`, `rejected`, or `incomplete`. It accepts only
the fixed roles above; `qwen3-coder:30b` is explicitly excluded, while `qwen3-vl:8b` remains a
non-selectable control. Reports must use the local-only v2 contract and include an RTX 3090, Ollama
version, evaluated model digest, and matching configured role model in their recorded runtime evidence.
The P2-00 comparison gate then enforces the frozen fixture, trial count, cold-start state, reliability,
critical-field coverage/accuracy, and latency. Missing expected candidate reports leave the overall
record `incomplete`; a recorded acceptance is decision evidence only and does not change defaults.

After an explicitly authorized private 3090 trial, write the outcome beneath the private mounted data
volume, for example:

```bash
python -m bourbonbook.model_evaluation \
  --config /data/benchmarks/evaluations/p2-01-input.json \
  --output /data/benchmarks/evaluations/p2-01-result.json
```

The validation agent may make only contained fixes within the action's scope, must add or correct
tests for every defect it fixes, and must report the focused commands plus `make coverage`. A
validation failure returns work to that action; it never permits the next action to begin. Before a
pull request is opened, the repository review lifecycle still applies: a commit-bound
`bourbonbook_reviewer` and `pr_validator` must both pass on the final candidate commit.

## Unraid runbook

Run inside the Bourbon Book container (or an equivalent image with the same code and `/data` mount).
These commands read the selected collection and call the configured provider; they never update the
bottles or expose a web endpoint.

```bash
# First, determine the owner ID or username in the app's admin UI.
python -m bourbonbook.benchmark_cli export \
  --owner YOUR_OWNER_ID \
  --output /data/benchmarks/fixtures/collection-v1

# If benchmarking local Ollama, unload its selected model immediately before this command.
# This is a live benchmark: it uses the active ANALYSIS_PROVIDER and can incur OpenAI costs.
python -m bourbonbook.benchmark_cli run \
  --fixture /data/benchmarks/fixtures/collection-v1 \
  --output /data/benchmarks/reports/current-baseline.json \
  --cold-start-state unloaded

# After exactly one change, produce a candidate report and enforce both speed and accuracy.
python -m bourbonbook.benchmark_cli compare \
  --baseline /data/benchmarks/reports/current-baseline.json \
  --candidate /data/benchmarks/reports/candidate.json
```

The fixture and report directories are intentionally under the mounted data volume and ignored by
Git. Back them up with the rest of the private app data, not in the repository.

For local development from a repo checkout, the same benchmark files live under
`data/benchmarks/` because `DATA_DIR` defaults to `./data`. The convenience targets are:

```bash
make benchmark-export BENCHMARK_OWNER=YOUR_OWNER_ID_OR_USERNAME
make benchmark-run
make benchmark-compare
```
