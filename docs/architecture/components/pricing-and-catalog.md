# Component Design: Pricing & Catalog

Modules: `bourbonbook/catalog.py`, `bourbonbook/qdrant_prices.py`, `bourbonbook/catalog_extract.py`,
`bourbonbook/catalog_cli.py`, pricing-orchestration functions in `bourbonbook/main.py`
Governing ADR: [ADR 0002: Local-First Pricing Catalog](../../adr/0002-local-first-pricing-catalog.md)
Related: [HLDD](../hldd.md) · [AI analysis](ai-analysis.md)

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
    if not force:
        cached = cached_catalog_price(session, bottle)                    # Tier 1
        if cached: ...; return "cached"
        matched = await qdrant_catalog_price(session, bottle, price_index)  # Tier 2
        if matched: ...; return "local_match"
    prices, sources, status = await search_bottle_prices(...)             # Tier 3
    apply_price_search(bottle, prices, sources)
    if status == "complete":
        cached = cache_catalog_price(session, bottle, prices, sources)    # writeback
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
- **Tier 3 (OpenAI grounded web search)**: only on a Tier 1/2 miss, or when `force=True` (the
  edit-page "refresh MSRP without re-analyzing the photo" action). See
  [AI analysis](ai-analysis.md) for the OpenAI adapter; the pricing-specific prompt instructs the
  model to check OHLQ.com first, reject size/edition mismatches, and return exactly one price with a
  source it actually retrieved.
- **Writeback**: any Tier 3 result with `status == "complete"` and an `http(s)` source URL is
  persisted into `CatalogPrice` (`cache_catalog_price()`) and, if Qdrant is enabled, upserted into
  the vector index — compounding the local-first hit rate over time.

## Grounding guarantee

`openai_provider.web_source_urls()` walks the response's `web_search_call` items and collects every
URL the model actually consulted, canonicalized (`canonical_url()`: lowercase scheme/host, no
trailing slash). The model's claimed `msrp_source_url` is accepted **only if** its canonical form
appears in that consulted-URL set. If not, or if `msrp`/`url` is missing, the result is rejected
(`status = "unavailable"`) rather than persisted — this prevents a plausible-but-uncited hallucinated
source from ever entering the shared catalog.

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
sometimes MSRP as a seed value). `verified_product()` does exact alias matching after
normalization; `verified_product_from_text()` does substring matching against OCR text. This feeds
[AI analysis](ai-analysis.md)'s `enrich_from_verified_catalog()` and is distinct from the dynamic
`CatalogPrice` table, which stores crowd/admin/OpenAI-sourced *prices*, not identity facts.

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

## Offline bulk ingestion (`catalog_extract.py` + `catalog_cli.py`)

Operator-invoked only, never reachable from an HTTP route on behalf of an untrusted user:

- `scripts/extract_catalog_screenshots.py` reads local PNG/JPEG/PDF files. PDFs are rasterized page
  by page via PyMuPDF (`document_chunks()`); tall image screenshots are sliced into overlapping
  vertical chunks (`image_chunks()`, default 2400px height, 120px overlap) so long price-list
  screenshots aren't truncated by model context limits.
- Each chunk goes to the local Ollama vision model (`OLLAMA_VISION_MODEL or OLLAMA_MODEL`) via
  `extract_chunk()`, prompted to use the sale "Now" price (not crossed-out prices) and skip
  incomplete cards.
- `catalog_extract.parse_catalog_items()` defensively parses the model's JSON (strips ```json
  fences), validating non-empty names, a canonical size (`canonical_size()` — normalizes e.g.
  `750ML`), and a bounded price (`parse_price()`, rejects values outside `(0, 100000)`).
  `deduplicate_catalog_items()` dedupes across overlapping chunk crops.
- `catalog_cli.ingest_jsonl()` validates each JSONL record (`catalog_record()` — requires positive
  `msrp`, an `http(s)` `url` unless `--allow-local-extract`, and a `YYYY-MM-DD`
  `price_updated_at`), upserts by `(product_key, size_key)`, and keeps Qdrant synced live.
  `reindex()` rebuilds the entire Qdrant index from all `CatalogPrice` rows.

> **Admin import workflow:** `/admin/catalog-import` stages bounded local uploads into the durable
> queue. The single local worker extracts review proposals, and an administrator may edit, exclude,
> retry failed work, and atomically apply approved rows. SQLite commits first; Qdrant receives a
> best-effort post-commit refresh and can always be rebuilt with `reindex()`.

## Config knobs

| Setting | Default | Effect |
| --- | --- | --- |
| `QDRANT_URL` | unset (disabled) | Enables `QdrantPriceIndex`; all calls no-op if unset |
| `QDRANT_API_KEY` | unset | Sent as `api-key` header when set |
| `QDRANT_PRICE_COLLECTION` | `bourbonbook_prices` | Collection name |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | unset / `gpt-5.5` | Gates and configures Tier 3 |
| `OLLAMA_VISION_MODEL` | `qwen3.6:35b` | Vision model used by the screenshot-extraction workflow (`catalog_extract.py`); falls back to `OLLAMA_MODEL` when unset |
| `OLLAMA_MODEL` | `qwen3.6:35b` | Universal Ollama fallback for both vision and text calls app-wide, including this workflow |

`PRICE_CACHE_TTL` (90 days) and `USER_PRICE_OVERRIDE_TTL` (183 days) are hardcoded `main.py`
constants, not environment-configurable.

## Design properties worth preserving

- SQLite is always authoritative; Qdrant is always optional and rebuildable. Any change here must
  preserve "the app works correctly with `QDRANT_URL` unset."
- The dual-threshold acceptance (vector score **and** string similarity) on Tier 2, and the
  cited-source check on Tier 3, are both intentional false-positive guards — removing either would
  let inaccurate prices into a cache that's shared across every user and bottle of that product/size.
- This subsystem is intentionally simpler than the Phase 2 RAG roadmap in `docs/adr/plan.md`; do not
  conflate the two when reading status/audit language in `plan.md` against what's actually shipped.
