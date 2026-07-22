# ADR 0002: Local-First Pricing Catalog with Optional Qdrant Fuzzy Match

Status: Accepted
Date: 2026-07-21

This ADR narrows [ADR 0001](0001-current-architecture-baseline.md), which explicitly deferred
"RAG/Qdrant-related work" to a later ADR. It records the pricing/catalog subsystem that has since
been implemented (`bourbonbook/catalog.py`, `bourbonbook/qdrant_prices.py`,
`bourbonbook/catalog_extract.py`, `bourbonbook/catalog_cli.py`, the `catalog_prices` table added in
migration `0007_catalog_prices`, and the `/admin/catalog` and `/admin/catalog-import` admin
surfaces). This is a distinct, smaller decision from the larger Phase 2 RAG/evidence-pipeline
roadmap tracked in `docs/adr/plan.md` (dense-embedding retrieval, a governed multi-source evidence
schema, durable refresh jobs, and OpenAI-assisted source discovery), which remains unimplemented and
out of scope here.

## Context

Bourbon Book needs an approximate current MSRP for each bottle a user adds, but:

- Bottle photos and names alone are not reliable price evidence; a photo prompt is explicitly
  instructed to always return `msrp: null`.
- Calling OpenAI's grounded web search for every bottle view/edit is slow, costs money per call, and
  is unnecessary once a given product-and-size has already been priced once.
- Many users' collections contain repeated products (the same bottle size purchased by multiple
  users, or the same bottle re-priced after edits), so a shared, reusable local cache has a high hit
  rate over time.
- The single-process, single-SQLite-writer deployment model from ADR 0001 must remain the source of
  truth; any optional acceleration structure must degrade safely if it is absent, slow, or wrong.

## Decision

1. **SQLite (`CatalogPrice`) is the durable, shared price cache**, keyed by a normalized
   `(product_key, size_key)` pair (`catalog.catalog_price_key()` / `normalize_product_name()`), so
   every bottle of the same product and package size reuses one row regardless of owner.
2. **Lookups are local-first, in three tiers**, orchestrated by `main.refresh_prices()`:
   - **Tier 1 — exact SQLite match.** An exact `(product_key, size_key)` row younger than
     `PRICE_CACHE_TTL` (90 days) is used immediately, no network call.
   - **Tier 2 — optional Qdrant fuzzy match.** If `QDRANT_URL` is configured, a locally-hashed
     sparse vector over the product name (`qdrant_prices.sparse_text_vector()` — SHA-256 token
     hashing, no embedding API, no product data leaves the app) is queried for a same-size candidate.
     A match is accepted only if both the vector score (`>= 0.82`) **and** an independent
     `difflib.SequenceMatcher` string-similarity check (`>= 0.82`) agree, and the underlying SQLite
     row is still fresh. Qdrant is a *candidate generator*, never a sole source of truth.
   - **Tier 3 — OpenAI grounded web search**, used only on a Tier 1/Tier 2 miss (or an explicit
     forced refresh). The prompt instructs the model to check OHLQ.com first, reject size/edition
     mismatches, and return one price with a source it actually consulted. The response is accepted
     only if the cited URL is present in the model's own recorded `web_search_call` sources
     (`openai_provider.web_source_urls()` / `canonical_url()`), preventing an uncited/hallucinated
     source from being persisted.
3. **Every accepted Tier 3 (or user-entered) price is written back** into `CatalogPrice`
   (`main.cache_catalog_price()`) and, if Qdrant is enabled, upserted into the vector index
   (`QdrantPriceIndex.upsert()`) — so the local-first loop compounds: each OpenAI call benefits every
   future bottle sharing that product and size.
4. **A user-entered purchase price can seed or refresh the shared catalog** (`main.
   apply_user_purchase_price()`) when the matching entry is missing or older than 183 days, skipping
   the web-search path entirely — but a fresher catalog entry is never overwritten by a user's price.
5. **Qdrant is optional, rebuildable infrastructure, not the system of record.** Every
   `QdrantPriceIndex` method degrades to a no-op/`None`/`False` on any `httpx.HTTPError` or when
   `QDRANT_URL` is unset, so a missing or unreachable Qdrant never blocks pricing — it only forces a
   fallback to Tier 3. The entire collection is derivable from `CatalogPrice` rows at any time via
   `catalog_cli.reindex()` (`make price-catalog-reindex`).
6. **A separate offline bulk-import path exists** for operator-supplied price sheets:
   `catalog_extract.py` (chunking + Ollama vision extraction of PNG/JPEG/PDF screenshots) and
   `catalog_cli.ingest_jsonl()` (validated JSONL upsert, with `--allow-local-extract` permitting
   locally-extracted records to omit a source URL, unlike web-search-derived records which must have
   one). This path never browses the web and is invoked only by an operator (`make
   price-catalog-extract-screenshots` / `make price-catalog-ingest`), never by an HTTP route on
   behalf of an untrusted user. (`/admin/catalog-import` in `main.py` currently only accepts and
   validates the upload; it does not yet wire the extraction pipeline — see Consequences.)
7. **A small, hand-curated `VERIFIED_PRODUCTS` table in `catalog.py`** provides exact-alias
   short-circuits for a handful of well-known bottles' static *product metadata* (brand, mash bill,
   proof/ABV) — a separate concern from `CatalogPrice`, which stores *prices*, not product facts.

## Rationale

- Local-first pricing keeps the common case fast and free: a repeated product/size never needs a
  network call after its first successful price.
- The two-tier acceptance check on Qdrant (vector score *and* string similarity) compensates for the
  index using an unsupervised, non-semantic sparse hash rather than a trained embedding model — it
  is a fast candidate filter, not a confident semantic match on its own.
- Grounding OpenAI's citation against its own recorded search actions is a cheap, high-value guard
  against a structured-output model inventing a plausible-looking but unconsulted URL.
- Making Qdrant fully optional and rebuildable lets the app run with zero pricing-index
  infrastructure (SQLite-only) or with Qdrant for faster fuzzy matches, without a code branch or
  degraded correctness either way — consistent with ADR 0001's preference for operational
  simplicity in a home-lab deployment.
- Keeping this local-first cache separate from the Phase 2 RAG roadmap avoids conflating a small,
  already-shipped acceleration structure with the much larger, still-undecided evidence-governance
  system described in `docs/adr/plan.md` (dense embeddings, multi-source evidence types, a source
  registry, durable refresh jobs, scheduled fetching). That system may supersede this ADR's Tier 2/3
  behavior later, but only after the design checkpoint `plan.md` calls for.

## Consequences

- `CatalogPrice` correctness depends on `catalog_price_key()`'s normalization being stable; renaming
  or reformatting a product name changes its cache key and can fragment the cache until re-priced.
- The 90-day (catalog) and 183-day (user-price override) TTLs are hardcoded constants in `main.py`,
  not admin-configurable; changing them requires a code change and redeploy.
- Qdrant's sparse hashed vector is not a true semantic embedding; near-miss but differently-worded
  product names may fail to match even when a human would recognize them as the same product. This
  is an accepted trade-off for avoiding an embedding-model dependency and keeping product names from
  ever leaving the app.
- `/admin/catalog-import`'s UI currently only validates and accepts an upload; the actual
  `catalog_extract.py` extraction pipeline is wired only through the CLI (`make
  price-catalog-extract-screenshots`), not yet through that admin route. This is a known gap, not a
  design decision — closing it does not require a new ADR.
- This local-first cache and the Phase 2 RAG roadmap (`docs/adr/plan.md`) will eventually need to be
  reconciled; when that work resumes past the "post-RAG design checkpoint," it should explicitly
  state whether it replaces, wraps, or coexists with the mechanism described here.

## Alternatives Considered

1. **Always call OpenAI grounded search for pricing.** Rejected: slower, costs money per bottle
   view/edit, and ignores that most collections repeat products/sizes.
2. **Use Qdrant with real dense embeddings (Ollama `/api/embed`) instead of a local sparse hash.**
   This is effectively what the deferred Phase 2 RAG prototype (`plan.md` actions A03/A04) proposes.
   Deferred rather than rejected: it adds an embedding-model dependency and a larger schema/design
   surface that the team explicitly chose to prototype separately before committing production
   pricing behavior to it.
3. **Trust Qdrant's vector score alone as an accept/reject threshold.** Rejected: a hashed sparse
   vector can produce false-positive high scores for unrelated products; the added string-similarity
   check is a cheap, effective second gate.
4. **Skip URL-citation grounding and trust the model's returned URL directly.** Rejected: structured
   output does not guarantee the model actually retrieved that page; the sources of a recorded
   `web_search_call` are the only ones a request can validate.
5. **Make Qdrant required infrastructure.** Rejected: it would turn an optional accelerator into a
   deployment dependency, which conflicts with ADR 0001's preference for a simple, mostly-SQLite
   home-lab deployment.

## Operational Constraints

- `QDRANT_URL` must point to an internal-network-only endpoint; Qdrant must never be exposed through
  the public reverse-proxy route (consistent with `plan.md`'s cross-cutting SSRF/network guidance).
- Operators can safely wipe and rebuild the Qdrant collection at any time with `make
  price-catalog-reindex`; no data loss results because SQLite remains authoritative.
- `make price-catalog-ingest PRICE_CATALOG=<path>` and `make price-catalog-extract-screenshots` are
  operator-invoked, offline maintenance actions, not user-facing routes.

## Supersession Criteria

This ADR is narrowed or superseded by a future ADR if the application:

- replaces the sparse-hash Qdrant index with a trained-embedding retrieval index,
- makes Qdrant a required (non-optional) dependency,
- changes the tiered local-first-then-OpenAI pricing order or acceptance thresholds materially,
- introduces the Phase 2 governed multi-source evidence/source-registry/durable-job system described
  in `docs/adr/plan.md`, or
- wires `/admin/catalog-import` to the extraction pipeline in a way that changes its trust model
  (e.g., accepting untrusted user uploads rather than operator-supplied files).

## Cross-links

- [ADR 0001: Current Architecture Baseline](0001-current-architecture-baseline.md)
- [C1 System Context](../architecture/c1-system-context.md)
- [C3 Components](../architecture/c3-components.md)
- [Pricing and catalog component design](../architecture/components/pricing-and-catalog.md)
- [Roadmap / Phase 2 plan](plan.md)
