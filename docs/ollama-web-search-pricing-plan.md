# Ollama Web-Search Pricing + Catalog-First Refresh Plan

## Context

Branch `feature/pricing-not-using-catalog` already stops the AI analysis step from ever
supplying MSRP (photo/name prompts force `msrp: null`, and `apply_analysis`/`merge_analysis`
only accept a catalog-verified MSRP via the `allow_msrp` flag). What's left, from the design
discussion that led here:

1. Today, only OpenAI's `search_prices()` (`bourbonbook/openai_provider.py`) can do a real,
   grounded live web lookup for MSRP — it uses the Responses API's built-in `web_search` tool.
   Ollama has no equivalent; `bourbonbook/ollama.py` only ever calls the single-shot
   `/api/generate` endpoint with no tools. The goal is to give Ollama the same ability.
   Ollama Cloud (ollama.com) exposes hosted `/api/web_search` and `/api/web_fetch` REST endpoints
   (confirmed via the `mcp__ollama__ollama_web_search` / `ollama_web_fetch` tool schemas available
   in this environment — both gated on `OLLAMA_API_KEY`; `web_search` takes `query`, `web_fetch`
   takes `url`). These aren't model capabilities; they're plain HTTP endpoints a tool-calling loop
   can call from either provider path.
2. `refresh_prices(force=True)` — used by the "look up internet pricing" button — currently skips
   the local catalog/Qdrant lookup entirely and jumps straight to a live web search. Confirmed
   desired behavior: it should still check local data first (ignoring the normal 90-day freshness
   window, since the user explicitly asked for a refresh), and only go to the web if there's no
   local record at all.
3. Confirmed: the add-bottle flow keeps its existing behavior — check catalog/Qdrant (fresh only),
   then automatically fall through to a live web search if no match. No change needed there beyond
   the dispatch now also covering Ollama.

## Changes

### 1. Config — `bourbonbook/config.py`
Add `ollama_api_key: str | None = None` to `Settings`, sourced from `OLLAMA_API_KEY` in
`from_env` (same pattern as `openai_api_key`). This is the Ollama **Cloud** key used for the
hosted `/api/web_search` and `/api/web_fetch` endpoints — distinct from `ollama_url`, which stays
pointed at the self-hosted inference server. Add `OLLAMA_API_KEY=` to `.env.example` next to the
other `OLLAMA_*` vars.

### 2. Shared helpers — `bourbonbook/analysis.py`
Extract two things currently private to `openai_provider.py` so both providers can share them
without duplication:
- `canonical_url(value: str) -> str` — move here as-is.
- `price_search_prompt(name: str, *, size: str | None) -> str` — move the existing OHLQ-first
  prompt text here (currently the literal string inside `openai_provider.search_prices`)
  unchanged. Both providers import and use the same prompt.

### 3. New module — `bourbonbook/ollama_search.py`
`async def search_prices(name, settings, *, size=None) -> tuple[dict[str, float], list[dict], str]`
— same return contract as `openai_provider.search_prices`.

- Guard: no `settings.ollama_api_key` → log + return `({}, [], "unavailable")`, mirroring
  `openai_provider.search_prices`'s `openai_api_key` guard.
- Build the shared `price_search_prompt(name, size=size)`, plus instructions to use the
  `web_search`/`web_fetch` tools and reply with a single JSON object
  (`msrp`, `msrp_source_title`, `msrp_source_url`, `msrp_basis`) once satisfied.
- Tool-calling loop against `{settings.ollama_url}/api/chat` (reuse `ollama_client_session()`
  from `provider_clients.py`), model = `settings.ollama_text_model or settings.ollama_model`,
  `tools=[web_search_tool, web_fetch_tool]` (OpenAI-style function schema, which Ollama's
  `/api/chat` accepts). Cap at 4 tool-call rounds to bound cost/latency.
  - On a `tool_calls` response: execute each call for real via `httpx` POST to
    `https://ollama.com/api/web_search` or `/api/web_fetch` with
    `Authorization: Bearer {ollama_api_key}`; append the assistant tool-call message and a
    `role: "tool"` result message to the conversation; track every URL seen (search results'
    `url` fields, and any fetched `url`) in a `consulted_urls` set (canonicalized).
  - On a plain content response: treat it as final, parse as JSON.
- Grounding guard (mirrors `openai_provider.web_source_urls` + the existing
  `search_prices`/`prices["msrp"]` gate): only accept the result if `msrp` is a number, `url` is
  present, and `canonical_url(url) in consulted_urls`. Otherwise treat as no price found — never
  trust a URL the model asserts but that didn't actually come back from a tool call.
- On any `httpx` error / bad JSON / missing key, log a warning and return `unavailable` — same
  resilience style as `ollama.py:request_analysis` (reuse `failure_context`/`connection_reason`/
  `bounded_error_type` helpers from `ollama.py` where they fit).
- Record usage via `current_usage_recorder()` (`provider="ollama", operation="price_search"`),
  same as the existing `ollama.py` and `openai_provider.py` calls.

### 4. Dispatch — `bourbonbook/analysis.py: search_bottle_prices`
Replace the current hard `if not settings.openai_api_key: return unavailable` gate with provider
dispatch matching the existing `_request_provider_analysis` pattern:
```python
async def search_bottle_prices(name, settings, *, size=None):
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import search_prices
        return await search_prices(name, settings, size=size)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama_search import search_prices
        return await search_prices(name, settings, size=size)
    return {}, [], "unavailable"
```
Each provider already self-gates on its own required credential.

### 5. `refresh_prices` — `bourbonbook/main.py`
Thread a `require_fresh` flag through the two local-lookup helpers instead of skipping them on
`force`:
- `cached_catalog_price(session, bottle, *, require_fresh: bool = True)` — only apply the
  `catalog_price_is_fresh(price)` check when `require_fresh` is True.
- `qdrant_catalog_price(session, bottle, price_index, *, require_fresh: bool = True)` — same
  change to its freshness check.

`refresh_prices` becomes:
```python
async def refresh_prices(session, bottle, settings, *, force=False, price_index=None):
    if not bottle.name or bottle.name == "Untitled bottle":
        return "unavailable"
    cached = cached_catalog_price(session, bottle, require_fresh=not force)
    if cached:
        apply_price_search(bottle, {"msrp": cached.msrp}, [catalog_price_source(cached)])
        return "cached"
    matched = await qdrant_catalog_price(session, bottle, price_index, require_fresh=not force)
    if matched:
        apply_price_search(bottle, {"msrp": matched.msrp}, [catalog_price_source(matched)])
        return "local_match"
    prices, sources, status = await search_bottle_prices(bottle.name, settings, size=bottle.size)
    ...  # unchanged
```
This one change satisfies both confirmed behaviors: add-flow (`force=False`) still requires
freshness before reuse and falls through to live search automatically; the "look up internet
pricing" button (`force=True`) now checks local data first at any age, only reaching the web when
there's truly no local record.

### 6. Tests
- `tests/test_ollama_search.py` (new): mock the `/api/chat` tool loop and the cloud
  `web_search`/`web_fetch` HTTP calls (monkeypatch the client, matching how `test_analysis.py`
  and `test_app.py` already fake providers). Cover: no `OLLAMA_API_KEY` → unavailable; a
  search → final-answer round trip that yields a price; a model-asserted URL that never appeared
  in tool results → dropped (grounding guard still holds for Ollama).
- `tests/test_analysis.py`: extend the `search_bottle_prices` tests to cover `analysis_provider =
  "ollama"` dispatch (currently only OpenAI dispatch is implied by the old hard gate).
- `tests/test_app.py`: extend the existing `refresh_prices` tests
  (`test_ohlq_price_cache_is_shared_between_matching_bottles`,
  `test_openai_fallback_price_is_persisted_for_local_reuse`,
  `test_qdrant_match_reuses_a_fresh_local_catalog_price`) with a case where `force=True` against a
  *stale* cached price still returns `"cached"` instead of hitting the web, and a case confirming
  `force=True` still returns `"cached"`/`"local_match"` for a fresh one (no behavior regression).

## Verification
- `pytest -xvs` for the new/updated test files.
- Manual sanity check with `OLLAMA_API_KEY` unset: Ollama price search still cleanly reports
  unavailable rather than erroring, matching today's OpenAI-key-unset behavior.
