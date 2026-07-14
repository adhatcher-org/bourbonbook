# AI Performance and Accuracy Plan

This plan is a separate, evidence-first workstream alongside the RAG roadmap in `plan.md`.
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

1. Re-run the unchanged 1070 Ti fixture/report on the 3090 after confirming GPU passthrough,
   sustained thermals, and model persistence.
2. Benchmark `qwen2.5vl:7b`, `qwen3-vl:8b`, and a viable Gemma 3 12B quantization.  Select a model
   only through the benchmark gate.
3. Add a bounded GPU job queue and limited concurrency only after measuring VRAM under simultaneous
   analysis.  Record queue time separately.
4. If concurrent requests justify it, perform a separate vLLM evaluation; retain Ollama unless the
   same benchmark proves a vLLM candidate meets the gate.
5. Move analysis to durable background jobs with progress UI and user confirmation for low-confidence
   fields.  Benchmark the end-to-end user-visible completion time before enabling it.

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
