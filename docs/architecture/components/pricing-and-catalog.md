# Component Design: Pricing & Catalog

Modules: `bourbonbook/catalog.py`, `bourbonbook/qdrant_prices.py`, `bourbonbook/catalog_cli.py`,
pricing-orchestration functions in `bourbonbook/main.py`
Governing ADR: [ADR 0002: Local-First Pricing Catalog](../../adr/0002-local-first-pricing-catalog.md)
Related: [HLDD](../hldd.md) · [AI analysis](ai-analysis.md) ·
[Catalog import pipeline](catalog-import.md)

## Responsibility

Resolve a current MSRP for a bottle as cheaply and reliably as possible, favoring a shared local
cache over a paid, slower, groundable-but-fallible web search — and keep that cache warm for every
future bottle of the same product and size. See ADR 0002 for the full rationale; this document
covers the implementation.

## The three-tier resolution (`main.refresh_prices()`)

```python
async def refresh_prices(session, bottle, settings, *, force=False, price_index=None) -> str:
    if not bottle.name or bottle.name == "Untitled bottle":
        return "unavailable"
    cached = cached_catalog_price(session, bottle, require_fresh=not force)  # Tier 1
    if cached:
        ...
        return "cached"
    matched = await qdrant_catalog_price(  # Tier 2
        session, bottle, price_index, require_fresh=not force
    )
    if matched:
        ...
        return "local_match"
    prices, sources, status = await search_bottle_prices(...)  # Tier 3
    apply_price_search(bottle, prices, sources)
    if status == "complete":
        cached = cache_catalog_price(session, bottle, prices, sources)  # writeback
        if cached and price_index:
            await price_index.upsert(cached)
    return status
```

- **Tier 1 (exact SQLite match)**: `cached_catalog_price()` computes
  `catalog.catalog_price_key(bottle.name, bottle.size)` (lowercased, apostrophes stripped,
  non-alphanumerics collapsed to single spaces) and does an exact `CatalogPrice` lookup, accepted
  only if `catalog_price_is_fresh()` — `PRICE_CACHE_TTL = 90 days`.
- **Tier 2 (Qdrant fuzzy match)**, only if a `price_index` is supplied and enabled:
  `qdrant_catalog_price()` requires `match.score >= 0.82` from Qdrant **and** a `difflib
  .SequenceMatcher` string-similarity ratio `>= 0.82` between the query and the matched record's
  product key **and** the underlying row still fresh. The vector score alone is never trusted.
- **What `force=True` means**: it relaxes the *freshness* requirement on Tiers 1 and 2
  (`require_fresh=not force`) — it does **not** skip them. The edit-page "look up internet pricing"
  action therefore reuses an existing local price of any age before spending a web search, and only
  falls through to Tier 3 when the local catalog genuinely has nothing for that product and size.
  (This is a deliberate change from the earlier behavior, where `force=True` jumped straight to the
  provider.)
- **Tier 3 (grounded web search, provider-dispatched)**: only on a Tier 1/2 miss.
  `analysis.search_bottle_prices()` selects the adapter from `ANALYSIS_PROVIDER` —
  `openai_provider.search_prices()` (OpenAI's hosted web-search tool) or
  `ollama_search.search_prices()` (a tool-calling loop against Ollama Cloud). See
  [AI analysis](ai-analysis.md) for both adapters. The shared pricing prompt
  (`analysis.price_search_prompt()`) instructs the model to use producer listings, official state
  price books, or reputable whiskey publications -- explicitly excluding `ohlq.com` -- reject
  size/edition mismatches, and return exactly one USD price with a source it actually retrieved.
- **Writeback**: any Tier 3 result with `status == "complete"` and an `http(s)` source URL is
  persisted into `CatalogPrice` (`cache_catalog_price()`) and, if Qdrant is enabled, upserted into
  the vector index — compounding the local-first hit rate over time.

## Grounding guarantee

Both Tier 3 adapters enforce the same rule with the same helper, `analysis.canonical_url()`
(lowercase scheme/host, no trailing slash):

- **OpenAI**: `openai_provider.web_source_urls()` walks the response's `web_search_call` items and
  collects every URL the model actually consulted.
- **Ollama**: `ollama_search.py` accumulates a `consulted_urls` set as it executes the model's
  `web_search` and `web_fetch` tool calls against Ollama Cloud — result URLs from a search, and the
  fetched URL itself from a fetch.

Either way the model's claimed `msrp_source_url` is accepted **only if** its canonical form appears
in that consulted-URL set. If not, or if `msrp`/`url` is missing or non-numeric, the result is
rejected (`status = "unavailable"`) rather than persisted — this prevents a plausible-but-uncited
hallucinated source from ever entering the shared catalog. The Ollama loop additionally caps itself
at `MAX_TOOL_ROUNDS = 4` and returns `unavailable` if the model has not settled by then.

## User-entered price override

`apply_user_purchase_price()` runs **before** `refresh_prices()` in the add-bottle flow. If the user
typed a purchase price and the matching catalog entry is missing or older than
`USER_PRICE_OVERRIDE_TTL = 183 days`, it writes the user's price into `CatalogPrice` (title
"User-entered purchase price", no URL) and upserts it into Qdrant, skipping the web-search tier
entirely for that bottle (`price_status = "user_price"`). A fresher catalog entry is never
overwritten by a user-entered price.

## `catalog.py`: verified products (a separate concern from pricing)

`VERIFIED_PRODUCTS` is a small, hand-curated dict of well-known bourbons (Blanton's variants, Weller
variants, New Riff 8yr, Eagle Rare 10, E.H. Taylor Small Batch, Buffalo Trace), each with alias
strings and a `values` dict of static *product metadata* (brand, mash bill, proof/ABV, size —
sometimes MSRP as a seed value). Matching is three-stage:

1. `verified_product()` tries exact alias equality after `normalize_product_name()`.
2. On a miss it falls back to `fuzzy_verified_product()`, which scores the normalized input against
   every alias with `difflib.SequenceMatcher` and accepts the best at
   `FUZZY_MATCH_THRESHOLD = 0.82` — the same threshold already validated against cross-product
   collisions for price matching. This exists because OCR and model transcription routinely drop or
   mangle a character in an otherwise obvious name.
3. `verified_product_from_text()` does substring matching against raw OCR text (exact aliases only,
   no fuzzy fallback — a substring scan over long OCR text has too many false-positive
   opportunities).

This feeds [AI analysis](ai-analysis.md)'s `enrich_from_verified_catalog()` and is distinct from the
dynamic `CatalogPrice` table, which stores admin/user/provider-sourced *prices*, not identity facts.

## `qdrant_prices.py`: optional, rebuildable retrieval index

- `sparse_text_vector()`: a **local, self-hosted sparse vector** — tokenizes the normalized product
  name, hashes each token with SHA-256, takes the first 4 bytes as an index, counts occurrences,
  L2-normalizes. No embedding API call, no product data ever leaves the app.
- `ensure_collection()`: idempotently creates the Qdrant collection
  (`{"vectors": {}, "sparse_vectors": {"product_text": {}}}`) if missing; degrades to `False`/a
  warning log on any `httpx.HTTPError` rather than raising.
- `upsert(price)`: point keyed by `CatalogPrice.id`, payload `{application, catalog_price_id,
  product_key, size_key}`.
- `find(product_key, size_key)`: sparse-vector query filtered by `application=bourbonbook` and exact
  `size_key`, `limit=1`, returns a `PriceMatch(catalog_price_id, score)` or `None`.
- **Every method degrades to a no-op on failure** (missing config, HTTP error, unset URL) — Qdrant
  being down never breaks pricing, it only forces the Tier 3 fallback.
- Because the whole index is derivable from `CatalogPrice` rows, it can be wiped and rebuilt any
  time via `catalog_cli.reindex()` (`make price-catalog-reindex`) — "rebuildable retrieval index,
  not source of truth" per the README and ADR 0002.

## Bulk ingestion

There are two ways `CatalogPrice` rows arrive in bulk, both ultimately reading price sheets with the
local Ollama vision model:

- **In-app, admin-driven** — `/admin/catalog-import`, a durable review-first queue. This is the
  primary path and has its own design doc: [Catalog import pipeline](catalog-import.md).
- **Offline CLI** — `scripts/extract_catalog_screenshots.py` (`make
  price-catalog-extract-screenshots`) runs the same `catalog_extract.py` extraction over local files
  and emits JSON Lines; `catalog_cli.ingest_jsonl()` then validates each record
  (`catalog_record()` — positive `msrp`, an `http(s)` `url` unless `--allow-local-extract`, and a
  `YYYY-MM-DD` `price_updated_at`), upserts by `(product_key, size_key)`, and keeps Qdrant synced
  live. `catalog_cli.reindex()` (`make price-catalog-reindex`) rebuilds the entire Qdrant index from
  all `CatalogPrice` rows.

The CLI path bypasses the review step by design — it is operator-invoked against files the operator
already trusts, and its records must carry provenance the admin UI supplies interactively.

## Config knobs

| Setting | Default | Effect |
| --- | --- | --- |
| `QDRANT_URL` | unset (disabled) | Enables `QdrantPriceIndex`; all calls no-op if unset |
| `QDRANT_API_KEY` | unset | Sent as `api-key` header when set |
| `QDRANT_PRICE_COLLECTION` | `bourbonbook_prices` | Collection name |
| `ANALYSIS_PROVIDER` | `ollama` | Selects the Tier 3 adapter as well as the analysis provider |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | unset / `gpt-5.5` | Gates and configures Tier 3 when `ANALYSIS_PROVIDER=openai` |
| `OLLAMA_API_KEY` | unset | Gates Tier 3 when `ANALYSIS_PROVIDER=ollama`; without it `ollama_search.search_prices()` returns `unavailable` immediately. Registered in `admin_config.CONFIG_FIELDS` as a write-only secret field |
| `OLLAMA_TEXT_MODEL` | falls back to `OLLAMA_MODEL` | Model used for the Ollama price-search tool loop |
| `OLLAMA_VISION_MODEL` | falls back to `OLLAMA_MODEL` | Vision model used by catalog price-sheet extraction (`catalog_extract.py`) |
| `OLLAMA_MODEL` | `qwen3.6:35b` | Universal Ollama fallback for both vision and text calls app-wide |
| `OLLAMA_NUM_CTX` | `4096` | Fallback/text context window for name analysis, refinement, and Ollama price chat |
| `OLLAMA_TEXT_NUM_CTX` | falls back to `OLLAMA_NUM_CTX` | Optional context window for a separate text model |
| `OLLAMA_VISION_NUM_CTX` | `32768` | Vision context window for bottle photos, catalog extraction, and warm-up |

`PRICE_CACHE_TTL` (90 days) and `USER_PRICE_OVERRIDE_TTL` (183 days) are hardcoded `main.py`
constants, not environment-configurable.

## Design properties worth preserving

- SQLite is always authoritative; Qdrant is always optional and rebuildable. Any change here must
  preserve "the app works correctly with `QDRANT_URL` unset."
- The dual-threshold acceptance (vector score **and** string similarity) on Tier 2, and the
  cited-source check on Tier 3, are both intentional false-positive guards — removing either would
  let inaccurate prices into a cache that's shared across every user and bottle of that product/size.
- **Both Tier 3 branches must stay implemented.** A pricing feature that only works under
  `ANALYSIS_PROVIDER=openai` is a regression against the local-first direction (ADR 0003); the
  grounding check in particular has to be enforced independently in each adapter, since neither can
  reuse the other's notion of "URLs actually consulted."
- `force=True` relaxing freshness rather than skipping the local tiers is a cost decision, not an
  implementation detail: reverting it turns every manual price refresh into a paid web search even
  when a perfectly good local row exists.
- This subsystem is intentionally simpler than the Phase 2 RAG roadmap in `docs/adr/plan.md`; do not
  conflate the two when reading status/audit language in `plan.md` against what's actually shipped.
