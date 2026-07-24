# Component Design: Model Evaluation & Benchmarking

Modules: `bourbonbook/benchmark_cli.py`, `bourbonbook/model_evaluation.py`
Related: [HLDD](../hldd.md) · [AI analysis](ai-analysis.md) ·
[ADR 0003: Fixed Local Model, No Benchmark Gate](../../adr/0003-fixed-local-model-no-benchmark-gate.md)

> **Superseded as a decision gate.** [ADR 0003](../../adr/0003-fixed-local-model-no-benchmark-gate.md)
> retired this tooling's role as a required approval process. Model selection for the photo and
> name-analysis roles is now a fixed operator/configuration decision (`qwen3.6:35b` for both, see
> [AI analysis](ai-analysis.md) and README/.env.example), not something gated on an `accepted`
> outcome from `evaluate_role_selection()`. The code described below is unchanged and still works
> exactly as documented — it's simply optional, non-blocking, ad hoc tooling now rather than a
> required step in adopting or changing a model.

## Responsibility

Offline, operator-invoked tooling to measure local Ollama model accuracy/latency against a real
user's private collection, and (historically) to deterministically decide which local model fills
each application role (photo analysis vs. name-only reconciliation) before it was adopted as a
default. Neither module is reachable from an HTTP route; both are CLI-only and excluded from the
request path by design.

## `benchmark_cli.py`

- **Fixture export** (`export_fixture()`): snapshots one owner's bottles (excluding shopping-list
  items) into a private, non-committed fixture directory — copies each photo, records `expected`
  ground truth for `PHOTO_FIELDS` (all analysis `FIELDS` except `msrp` — price is explicitly
  reference-only and never scored), and writes a `manifest.json` with a self-referential SHA-256
  digest. `load_fixture()` re-verifies both the manifest digest and every photo's hash before use,
  so a benchmark can't silently run against tampered or drifted fixture data.
- **Run** (`run_fixture()`): executes `analysis.analyze_bottle`/`analyze_bottle_name` against every
  fixture case, `--runs` times, for the selected `--operations` (`photo`, `name`, or both). The
  first case/operation is timed separately as a "cold start" sample, isolated from the main loop.
  Per-field comparison (`matches()`) uses tolerance for numeric fields (proof/ABV ±0.5, fill_level
  ±10), a canonicalized-millilitre comparison for size, and normalized-text exact match otherwise.
- **Summarize** (`summarize()`): per-field scored/matched/accuracy, p50/p95/max latency, overall
  accuracy.
- **Local-only enforcement** (`ensure_local_benchmark_settings()`): hard-refuses to run unless
  `analysis_provider == "ollama"` and no OpenAI key is present — benchmarks must be isolated from
  the paid/networked provider so results are reproducible and comparable.
- **Runtime evidence** (`collect_runtime_evidence()`): captures non-secret environment context —
  Ollama `/api/version`, `/api/ps` resident models (digest, VRAM), GPU inventory via `nvidia-smi`,
  configured model names — so a result can be tied to a specific, verifiable hardware/runtime state
  without being required for the run itself to succeed.
- **Acceptance gate** (`compare_reports()`): historically the mechanism a role-selection decision was
  based on; per ADR 0003 it is no longer required before adopting a model, but the comparison logic
  itself is unchanged and still useful for ad hoc "did this get better or worse" checks. Requires
  matching fixture manifest digest, run count, and cold-start state between a
  candidate and baseline report; then checks p50/p95 latency didn't regress, request/success counts
  didn't regress, and — for a fixed `CRITICAL_FIELDS` set per operation (photo:
  name/brand/proof/abv/size/status/fill_level; name: name/brand/proof/abv/size) — neither field
  coverage nor accuracy regressed.
- **CLI subcommands**: `export`, `run --live` (the `--live` flag is a required, explicit
  acknowledgment that it calls a real Ollama endpoint), `compare`, `upgrade-report` (migrates legacy
  v1 reports for inspection only — explicitly flagged non-comparable to current reports).

## `model_evaluation.py`

Builds on `benchmark_cli`'s report format to deterministically select which local model fills each
application role:

- `ROLE_CANDIDATES`: `photo → ("gemma4:26b",)`; `name → ("qwen3:30b-a3b", "qwen3.6:35b")` — a fixed,
  reviewed candidate list per role, not an open-ended search. This list is unchanged code and does
  **not** reflect the actual configured runtime model (`qwen3.6:35b`, used for both roles per ADR
  0003); it only governs what `evaluate_role_selection()` will score if it's run at all.
- Validates each report's schema/contract version (`REPORT_CONTRACT_VERSION =
  "benchmark-v2-local-only"`), provider (`ollama`), single-operation restriction, and
  `runtime_evidence` — specifically requiring an RTX 3090 in the recorded GPU list and a resident
  model digest matching the evaluated model, tying acceptance to a specific, verifiable hardware
  state rather than an unverified claim.
- `evaluate_role_selection()` walks every candidate, flags anything outside `ROLE_CANDIDATES` as
  `rejected`, applies the schema/runtime/comparison checks, and reports
  `accepted`/`rejected`/`incomplete` per candidate plus an overall outcome.

## Why this exists as its own component

Bourbon Book's core promise — accurate, useful vision analysis on consumer hardware — depends on
which local model is actually resident on the operator's GPU. This tooling was built so a model swap
could be a reviewable, evidence-backed decision rather than an untested config change, while keeping
that evaluation infrastructure fully separated from the request-serving path (no benchmark code runs
during a user's bottle-add request). ADR 0003 retired the "must be evidence-backed before adoption"
requirement — the hardware (RTX 3090) and provider (Ollama) are fixed regardless of any benchmark
outcome, so a benchmark result can no longer change that decision. The separation from the
request-serving path remains true and worth preserving regardless of whether the gate is enforced.

## Design properties worth preserving

- Benchmarks never call OpenAI and refuse to run if it's configured — this is enforced in code, not
  just documented, so a misconfigured environment can't silently produce a mixed-provider report.
  This still applies to any ad hoc use of the tooling post-ADR-0003.
- Fixture and report integrity checks (manifest digest, photo hashes, runtime-evidence matching) are
  designed to make "the model changed" and "the fixture/environment changed" distinguishable causes
  of a score change — useful for informal comparisons even though nothing requires running them now.
- `docs/adr/plan.md` (Phase 2, actions P2-00/P2-01, now marked **Retired**) tracks the specific known
  gaps in the benchmark semantics as of the last audit (e.g., counting only `complete` status as
  success, scoring fields not actually observable per operation). Those gaps were never fixed and no
  longer block anything; treat any report this tooling produces as informal, not decision-ready,
  unless someone deliberately repairs P2-00's known issues first.
