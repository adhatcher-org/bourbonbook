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
