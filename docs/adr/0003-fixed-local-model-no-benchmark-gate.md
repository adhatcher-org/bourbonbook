# ADR 0003: Fixed Local Model Selection (RTX 3090 + Ollama), No Benchmark Acceptance Gate

Status: Accepted
Date: 2026-07-22

This ADR narrows [ADR 0001](0001-current-architecture-baseline.md) and retires the benchmark-gated
model-role-selection process described in `docs/adr/plan.md` (actions P2-00 and P2-01, and the
"Phase 2 model and transaction rules"/"Local operation map" sections that depend on it).

## Context

The Phase 2 roadmap in `plan.md` established a two-step, evidence-gated process for choosing which
local Ollama model fills the photo-analysis role and the name-reconciliation role:

- **P2-00** ("Repair benchmark semantics, runtime evidence, and local-only enforcement") was meant
  to fix known defects in `benchmark_cli.py`'s scoring and enforce that benchmarks only ever exercise
  the local Ollama provider.
- **P2-01** ("Select local model roles on the 3090") was meant to run that repaired benchmark on the
  operator's actual RTX 3090 and accept a model for a role only if it passed a frozen
  accuracy/reliability/latency comparison (`benchmark_cli.compare_reports()`, enforced through
  `model_evaluation.evaluate_role_selection()` and its `ROLE_CANDIDATES` allow-list).

Per `plan.md`'s own audit (against commit `4e70330`), neither action ever reached a decision-ready
state: P2-00 remained **Outstanding** (the benchmark counted only `complete` status as success and
had no working runtime-evidence/local-only enforcement at audit time), and P2-01 remained blocked on
"a private, authorized 3090 report" that was never produced.

Independently of that stalled process, the operator has fixed the two variables the benchmark gate
existed to inform:

- **Hardware**: the RTX 3090 is the sole local-inference target (already reflected in `plan.md`'s
  "one GPU lane with capacity one" decision).
- **Provider**: Ollama is used for the application's analysis request path, not OpenAI.

With both fixed regardless of what any benchmark would show, a benchmark result could no longer
change the underlying decision — it could only add process before reaching a configuration that was
already going to be adopted. Gating on a benchmark that was itself never repaired to a working state
compounded the problem: the gate was blocking on evidence that didn't yet exist and couldn't yet be
trusted.

## Decision

1. **Local model selection for the photo-analysis and name-reconciliation roles is an ordinary
   operator/configuration decision** (`OLLAMA_VISION_MODEL`, `OLLAMA_MODEL`, `OLLAMA_TEXT_MODEL` in
   `.env`/README), not a benchmark-gated approval process.
2. **`qwen3.6:35b` is adopted directly for both roles** (`OLLAMA_VISION_MODEL` and the `OLLAMA_MODEL`
   fallback used for name analysis) on the strength of its confirmed capability set — `completion`,
   `vision`, `tools`, `thinking`, verified live against the operator's own Ollama server — rather
   than a passing `benchmark_cli`/`model_evaluation` acceptance record.
3. **P2-00 and P2-01 are retired as blocking prerequisites.** No pull request, deployment, or
   configuration change involving a local model choice is required to produce or reference a
   `model_evaluation.evaluate_role_selection()` "accepted" record, a `benchmark_cli.compare_reports()`
   pass, or a captured 3090 benchmark report.
4. **The benchmark/evaluation tooling is not removed by this ADR.** `benchmark_cli.py` and
   `model_evaluation.py` remain in the repository as optional, non-blocking diagnostic tools the
   operator may run informally (e.g., to compare latency or accuracy out of curiosity). Whether to
   keep, simplify, or delete that tooling is a separate follow-up decision, not made here.
5. **`docs/adr/plan.md`'s Phase 2 model-role language is updated to match**: the "Phase 2 model and
   transaction rules" section and the "Local operation map and user-transaction timing" table no
   longer describe `qwen3:30b-a3b`/`qwen3.6:35b` as benchmark-challenger candidates for a role
   `gemma4:26b` alone was assumed to hold; they reflect the single fixed model actually configured.

## Rationale

- A gate is only useful if a "reject" outcome could plausibly change the decision. Here it could
  not: the operator had already committed to the 3090 and Ollama independent of any benchmark
  result.
- The benchmark tooling itself had not reached a working, decision-ready state (P2-00 remained
  "Outstanding" against its own repair goal), so the gate was, in practice, blocking indefinitely on
  evidence that didn't exist yet.
- Removing the gate reduces process overhead sized for a larger team's release process down to what
  a single-operator home-lab deployment actually needs — consistent with ADR 0001's preference for
  operational simplicity over process weight the deployment model doesn't require.

## Consequences

- Model swaps going forward are ordinary configuration changes (README/.env.example update +
  restart), not benchmark-gated releases requiring a captured report.
- The project gives up the objective, evidence-backed accuracy/latency comparison
  `benchmark_cli`/`model_evaluation` were designed to produce before a role change. If the operator
  later wants that signal for a specific decision, the tooling still exists for ad hoc, non-blocking
  use.
- `docs/adr/plan.md`'s P2-00/P2-01 rows are marked retired rather than deleted, preserving the
  historical record of what was attempted and why it didn't reach completion.
- Downstream Phase 2 actions that were listed as "Blocked by P2-01" (P2-02A onward) are **not**
  automatically unblocked by this ADR — their dependencies were about hardening the local
  analysis/catalog contract generally, not specifically about the benchmark gate, and re-sequencing
  them is a separate planning decision left to the operator.
- If a second GPU or a second specialized model per role is introduced later, that would be a new
  configuration decision; reintroducing a formal comparison process at that point would warrant a
  new ADR rather than reviving this one's retired gate.

## Alternatives Considered

1. **Keep the gate but mark it advisory/non-blocking.** Rejected: a gate nobody is required to pass
   is not meaningfully a gate, and leaving the old "must pass" language in `plan.md` while nobody
   enforces it would be more confusing than retiring it explicitly.
2. **Finish repairing P2-00 first, then run P2-01 for real before changing any defaults.** Rejected
   by the operator: the hardware/provider decision was already final regardless of what a completed
   benchmark would show, so finishing the repair first would have delayed an already-decided
   configuration change for no decision-relevant benefit.
3. **Delete `benchmark_cli.py`/`model_evaluation.py` now.** Not decided here; left as a separate,
   explicit follow-up so the tooling's fate is a deliberate choice rather than a side effect of this
   ADR.

## Supersession Criteria

This ADR is narrowed or superseded by a future ADR if the application:

- reintroduces a benchmark-gated (or any evidence-gated) model-adoption approval process,
- adds a second local-inference GPU or hardware target where a comparison would again be
  decision-relevant,
- changes the analysis provider away from a fixed Ollama-on-3090 model, or
- removes or materially repurposes `benchmark_cli.py`/`model_evaluation.py`.

## Cross-links

- [ADR 0001: Current Architecture Baseline](0001-current-architecture-baseline.md)
- [ADR 0002: Local-First Pricing Catalog](0002-local-first-pricing-catalog.md)
- [Phase 2 roadmap (plan.md)](plan.md)
- [Model evaluation & benchmarking component design](../architecture/components/model-evaluation-and-benchmarking.md)
- [AI analysis component design](../architecture/components/ai-analysis.md)
