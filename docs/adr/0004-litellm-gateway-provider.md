# ADR 0004: LiteLLM Gateway as a Third Analysis Provider

Status: Proposed
Date: 2026-09-02

This ADR extends [ADR 0001](0001-current-architecture-baseline.md)'s two-provider analysis boundary
and narrows nothing in [ADR 0003](0003-fixed-local-model-no-benchmark-gate.md): local model roles
remain fixed configuration, and this decision only changes how the app reaches them.

## Context

The operator now runs a LiteLLM proxy in front of the same local Ollama models the application has
always used. LiteLLM centralizes keys, routing, rate limits, and usage accounting across every tool
in the home lab, so pointing Bourbon Book at Ollama directly bypasses the layer everything else goes
through.

LiteLLM speaks the OpenAI `/chat/completions` surface, not Ollama's native `/api/generate`. That
leaves three plausible shapes:

- **Reuse the OpenAI provider with a custom base URL.** `openai_provider.py` is built on the
  Responses API (`client.responses.parse`) for its Structured Outputs. LiteLLM does not offer a
  Responses surface for Ollama-backed routes, so this fails at the first call.
- **Add a transport flag to the Ollama provider.** One switch would cover every Ollama call site,
  but it conflates the host with the wire format and forces one set of model names to mean two
  different things: a LiteLLM route is an alias its own config defines (`ollama/qwen3.6:35b`, or
  anything the operator names it), not the raw Ollama tag.
- **Add a third provider.** More dispatch points, but each configuration surface stays honest about
  what it names, and the direct-Ollama path is untouched for anyone not running a gateway.

The roles themselves do not change either way. Vision, text, and a shared fallback remain the three
fixed roles ADR 0003 established, and each still needs its own context window; a gateway adds one
budget the native path never exposed, an output-token cap, because a proxy is where request budgets
are normally enforced.

## Decision

1. **`ANALYSIS_PROVIDER=litellm` is a third provider**, implemented in `bourbonbook/litellm_provider.py`
   against the OpenAI `/chat/completions` surface. `ollama` and `openai` are unchanged.
2. **The gateway has its own configuration namespace** mirroring the Ollama roles: `LITELLM_URL`,
   `LITELLM_API_KEY`, `LITELLM_MODEL`, `LITELLM_VISION_MODEL`, `LITELLM_TEXT_MODEL`,
   `LITELLM_NUM_CTX`, `LITELLM_VISION_NUM_CTX`, `LITELLM_TEXT_NUM_CTX`, and the optional output caps
   `LITELLM_MAX_TOKENS`, `LITELLM_VISION_MAX_TOKENS`, `LITELLM_TEXT_MAX_TOKENS`. All are
   admin-editable and follow the same fallback rules the Ollama roles use.
3. **Ollama-native options ride along as request fields.** The resolved context window is sent as a
   top-level `num_ctx`, which LiteLLM forwards to the backing provider. Output caps are sent as
   `max_tokens`, omitted entirely when unset.
4. **`LITELLM_URL` names the OpenAI-compatible base.** A bare origin gains `/v1`; a URL that already
   carries a path is used verbatim, so a proxy mounted elsewhere still works. Normalization happens
   both in `Settings.from_env` and in the admin form, so the saved value is the one that runs.
5. **The API key is optional.** A self-hosted LiteLLM may run with no master key, so the
   `Authorization` header is omitted rather than sent empty. Only `LITELLM_URL` is required, and
   selecting the provider without one is a validation error rather than a silent no-op.
6. **Catalog extraction follows `ANALYSIS_PROVIDER`** rather than reaching past it to Ollama. A
   gateway-only deployment would otherwise have a working add-bottle flow and a catalog importer
   aimed at a host that may not be reachable.
7. **The second-pass refinement applies to both local-model providers.** LiteLLM fronts the same
   local models, so `analysis.uses_local_models()` — not a bare `== "ollama"` test — decides whether
   a partial result earns a refining call. Metered remote APIs still get one pass.
8. **Grounded price search keeps its own key.** The chat model is proxied, but `web_search` and
   `web_fetch` remain Ollama Cloud's and still require `OLLAMA_API_KEY`. Without it the answer is
   `unavailable`, which leaves the local-first catalog path from ADR 0002 fully intact.

## Rationale

- A separate provider keeps each configuration surface truthful. Model names, context windows, and
  token budgets are per-gateway facts, and a transport flag would have made one set of settings mean
  two different things depending on a mode.
- Routing catalog extraction and the vision warm-up through the same switch is what makes the
  provider a real choice rather than a partial one; ADR 0001's provider boundary is only meaningful
  if every model call respects it.
- Reusing the Ollama Cloud tool loop for price search avoids inventing a second grounding mechanism
  with different provenance rules. Provenance is a pricing rule, not a transport detail.

## Consequences

- Every provider dispatch point now has three branches instead of two. The unknown-provider default
  (`unavailable`) still covers a misconfigured value, so an unrecognized name degrades rather than
  raising.
- The usage ledger and the `bourbonbook_ai_*` metrics gain a `litellm` provider label. Existing
  dashboards that filter on `ollama` or `openai` will not show gateway traffic until updated.
- Requests now depend on LiteLLM's parameter forwarding for `num_ctx`. If a future LiteLLM drops
  unrecognized parameters by default, the context window would silently fall back to the model
  default; the symptom would be truncated long prompts, not an error.
- A gateway-only deployment with no `OLLAMA_API_KEY` has no web price search at all. That is a
  deliberate narrowing, not a regression: an ungrounded price is worse than no price.
- `OLLAMA_URL` stays required by the admin form even for a LiteLLM deployment, matching the existing
  behavior for an OpenAI one. Tightening that is a separate change.

## Alternatives Considered

1. **Point the OpenAI provider at LiteLLM via a base URL.** Rejected: it depends on the Responses
   API, which LiteLLM does not serve for Ollama-backed routes.
2. **A transport flag on the Ollama provider.** Rejected for the naming collision described above,
   and because it would have made the direct path harder to reason about for deployments that never
   adopt a gateway.
3. **Proxy price search through LiteLLM as well.** Not possible today: the grounded research tools
   are an Ollama Cloud service, not a model capability the gateway can route.

## Supersession Criteria

This ADR is narrowed or superseded by a future ADR if the application:

- adds a fourth analysis provider, or collapses these three behind a single OpenAI-compatible
  adapter,
- moves grounded price search off Ollama Cloud, or
- makes the gateway the only supported path to local models.

## Cross-links

- [ADR 0001: Current Architecture Baseline](0001-current-architecture-baseline.md)
- [ADR 0002: Local-First Pricing Catalog](0002-local-first-pricing-catalog.md)
- [ADR 0003: Fixed Local Model Selection](0003-fixed-local-model-no-benchmark-gate.md)
