---
name: provider-evaluation
description: "Workflow for Bourbon Book AI and pricing provider work — Ollama, OpenAI, prompts, structured outputs, the local-first pricing path, Qdrant embeddings/retrieval, fallback behavior, and API usage accounting. Covers the provider dispatch contract, what may never leave the machine, and how to test without touching a live model. Use when editing analysis.py, ollama*.py, openai_provider.py, catalog*.py, qdrant_prices.py, provider_clients.py, or observability usage recording — or invoking $provider-evaluation."
---

## Context

Fixed facts about how providers work here:

**Dispatch.** `bourbonbook/analysis.py` is the single entry point. `_request_provider_analysis`
branches on `settings.analysis_provider` (`"ollama"` | `"openai"`), imports the provider module
lazily inside the branch, and returns `({}, "unavailable")` for anything else. Public surface:
`analyze_bottle`, `analyze_bottle_name`, `search_bottle_prices`, `warm_analysis_model`.
Provider modules are `bourbonbook/ollama.py`, `bourbonbook/ollama_search.py`,
`bourbonbook/openai_provider.py`; shared clients live in `bourbonbook/provider_clients.py`
(contextvar-scoped `openai_client_session` / `ollama_client_session`).

**The catalog outranks the model.** `enrich_from_verified_catalog` runs after every analysis; a
match returns status `"verified"` and short-circuits further model calls. Ollama-only refinement
(`_refine_analysis`) runs a second text pass just for missing fields. Normalization
(`normalize_analysis`, `reconcile_proof_and_abv`, `snap_size`) happens in application code, not the
prompt — a model answer is a proposal, never a persisted fact.

**Pricing is local-first, in this order.** Exact SQLite catalog match (`catalog_price_key` on
normalized name + size) → optional Qdrant fuzzy match for the *same size*
(`QdrantPriceIndex.find`) → only on a local miss, a grounded OpenAI web search that checks OHLQ
first. Every accepted result carries a consulted source URL and is written back to the local
catalog. **Qdrant is a rebuildable index, never the source of truth**
(`make price-catalog-reindex` rebuilds it from SQLite).

**Configuration** (`.env.example` / `bourbonbook/config.py`): `ANALYSIS_PROVIDER`, `OLLAMA_URL`,
`OLLAMA_MODEL`, `OLLAMA_VISION_MODEL`, `OLLAMA_TEXT_MODEL`, `OLLAMA_API_KEY`, `OPENAI_API_KEY`,
`OPENAI_MODEL`, `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_PRICE_COLLECTION`. Model roles are a fixed
operator decision per **ADR 0003** (`qwen3.6:35b` for both vision and text) — not benchmark-gated.
`benchmark_cli.py` / `model_evaluation.py` remain optional, non-blocking diagnostics.

**Usage accounting.** `AIUsageRecorder.record(...)` in `bourbonbook/observability.py` persists
provider, operation, model, success, bounded `error_type` (≤40 chars), duration, token counts, web
search calls, and optional internal user ID. It **must never** receive prompts, responses, bottle
names, emails, URLs, or keys. `bounded_error_type` exists precisely to keep exception text out of
the ledger. Retention is `API_USAGE_RETENTION_DAYS`; `/admin/usage` renders it.

## Procedure

### 1. Establish the current behavior

Read `analysis.py` end to end plus the provider module you're changing. Identify which of the four
public functions the change affects and what each provider branch currently returns — including the
status string, which callers switch on.

### 2. Implement

- **Keep dispatch centralized.** New provider behavior goes behind the `analysis.py` functions.
  Don't let routes, templates, or `bottle_processing.py` import a provider module directly.
- **Both branches, always.** Anything added to the OpenAI path needs a defined Ollama-path answer
  (real implementation, or an explicit `"unavailable"`), and vice versa. A code path that only works
  when `ANALYSIS_PROVIDER=openai` is a regression — ADR direction is local-first.
- **Fail soft.** A provider timeout, 5xx, malformed JSON, or unreachable host must degrade to the
  manual path: the photo is still saved and the review form still opens. Never a 500, never data
  loss, never a silently persisted empty bottle.
- **Structured outputs constrain shape, not truth.** Validate every value and its provenance in
  application code before persistence. Never widen a schema to "accept whatever the model returned."
- **Pricing rules.** Preserve the local-first order. Require exact product/release/edition/**size**
  matching before applying a price. Store the source URL and observation date. Keep MSRP, retailer
  asking price, completed sale, auction result, and user-reported price as distinct evidence types —
  never silently combine them. Return unavailable rather than inventing a price.
- **Untrusted input.** URLs and page content from users, models, and the web are hostile: validate
  scheme and host, block loopback/private/link-local destinations, cap redirects and response size,
  prevent DNS rebinding and SSRF. Respect `robots.txt` and site terms; never bypass a paywall or
  access control.
- **Prompts** live next to their provider (`PHOTO_PROMPT`, `name_prompt`, `price_search_prompt`,
  `analysis_prompt`). Change a prompt and the normalization/validation around it together — and say
  in your report that model output shape may shift.
- **Usage recording** on every new provider call: provider, operation, model, success, duration,
  bounded error type. Nothing sensitive. Add the matching metric if the operation is new.
- **Qdrant is optional.** Every Qdrant path must work when `QDRANT_URL` is unset
  (`QdrantPriceIndex.enabled` is False) and when the service is down. SQLite must remain fully
  usable without it.
- Update `.env.example`, `/admin/config` validation, README, and the usage/metrics surfaces whenever
  you add configuration or a new operation.

### 3. Test

**Deterministic tests must never reach OpenAI, Ollama, Qdrant, SMTP, or the web.** Use the injected
fakes and the contextvar client sessions in `provider_clients.py`; follow the patterns in
`tests/test_ollama.py`, `tests/test_ollama_search.py`, `tests/test_openai_provider.py`,
`tests/test_analysis.py`, `tests/test_qdrant_prices.py`, and `tests/test_catalog.py`.

Cover, at minimum:

- Both provider branches, plus the unknown-provider `"unavailable"` case.
- The catalog-verified short-circuit (no model call happens).
- Timeout / malformed-response / unreachable-host degradation.
- Pricing order: catalog hit → no Qdrant call, no web call; catalog miss + Qdrant hit → no web call.
- Qdrant disabled and Qdrant unreachable.
- A usage record is written with the expected bounded fields and **without** sensitive values.

```bash
make lint
make test
make coverage
make security
```

### 4. Live verification (optional, never in CI)

Only when explicitly asked, and never as part of the automated suite:
`make benchmark-run` / `make benchmark-compare` for ad hoc model comparison (non-blocking per ADR
0003), and the `e2e-bottle-test` skill for a real photo → analysis → edit-page run against a live
container. Note in your report that these hit real models and real data.

## Hard stops

- Never commit or log an API key, prompt, response, bottle name, email, or URL into the usage
  ledger, metrics, or logs.
- Never make a deterministic test call a live provider or the network.
- Never make Qdrant required, or treat it as the source of truth.
- Never persist a model-proposed price without an exact size match and a source URL.
- Never reintroduce a benchmark gate as a prerequisite for changing a model (ADR 0003).
- Never add a code path that works on only one provider without stating the other's behavior.
