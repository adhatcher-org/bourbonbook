# ADR 0005: Drop OHLQ as a Grounded Price Source

Status: Proposed
Date: 2026-09-02

This ADR narrows [ADR 0002](0002-local-first-pricing-catalog.md), which established the shared
pricing prompt and specified that it "instructs the model to check OHLQ.com first". ADR 0002 is
otherwise unchanged: pricing stays local-first, and provenance rules are untouched.

## Context

OHLQ was the preferred first source because Ohio's state price book is an authoritative, exact
per-size listing — precisely the evidence the provenance rules in ADR 0002 demand.

It no longer works. Ollama Cloud's `web_fetch` returns 404 for OHLQ URLs while fetching other hosts
normally, measured against the live service on 2026-09-02:

| request | result |
|---|---|
| `POST ollama.com/api/web_search` | 200 |
| `POST ollama.com/api/web_fetch` → `https://www.ohlq.com/` | 404 `{"error":"not found"}` |
| `POST ollama.com/api/web_fetch` → `https://www.ohlq.com/product/weller-antique-107` | 404 |
| `POST ollama.com/api/web_fetch` → `https://ollama.com/` | 200 |

OHLQ appears to block Ollama's fetcher. Because the prompt steered the model to OHLQ first, nearly
every grounded search began with a fetch that could not succeed. `ollama_search.run_cloud_tool()`
calls `raise_for_status()`, so that 404 propagated as an exception and aborted the entire price
search rather than being handed back to the model as a recoverable tool result — a live
`price_search` failure was observed at 54.6s with `http_status=404` from `ollama.com`.

A source the retrieval layer cannot read is not a source. Preferring it produced a reliable failure.

## Decision

1. **`price_search_prompt()` no longer names OHLQ as a source to query.** The ranked guidance is now
   the producer's official listing, an official state price book, or a reputable whiskey
   publication, with the same exact product-and-size requirement.
2. **The prompt explicitly excludes `ohlq.com`.** Naming it to rule it out is more reliable than
   silence, because the model would otherwise rediscover it through a plain web search and walk back
   into the unfetchable case.
3. **The change is global, not per-provider.** All three providers share one prompt, so the OpenAI
   branch loses OHLQ too, even though its hosted web search can reach the site. One pricing contract
   for every provider is the invariant worth keeping (ADR 0003's "both branches always have a
   defined answer" applied to prompts); a per-provider source list would make pricing evidence
   depend on which model happened to be configured.
4. **Nothing else about pricing changes.** Local-first order, the `catalog_prices` cache, the
   consulted-URL requirement, size matching, and the single-USD-value rule are all as ADR 0002 left
   them. Existing OHLQ-sourced rows in `catalog_prices` remain valid cached evidence; this decision
   governs new lookups only.

## Rationale

- The provenance rule in ADR 0002 already requires a source the model actually retrieved. OHLQ can
  no longer satisfy that through the Ollama path, so preferring it only guaranteed the search would
  fail before reaching a source that could.
- Excluding it by name costs one sentence and removes a failure mode that fires on essentially every
  request.

## Consequences

- Ohio-specific state pricing is no longer directly consulted. Producer listings and other official
  price books are less exactly Ohio-scoped, so some MSRP values may drift from the Ohio shelf price
  the collection previously recorded. The prompt still asks for the Ohio retail price.
- Historical OHLQ-sourced cache entries and their source URLs stay in the database and keep being
  served. The UI will therefore show OHLQ provenance for old rows and other sources for new ones.
- The underlying defect — one failed tool call aborting the whole tool loop — is **not** fixed by
  this ADR. It is merely no longer triggered by the common path. Any other unfetchable source will
  reproduce it, which conflicts with the "degrade, never fail hard" invariant and warrants a
  separate change.
- If OHLQ later becomes fetchable, restoring it is a prompt change plus a superseding ADR, not a
  silent revert.

## Alternatives Considered

1. **Keep OHLQ first and fix the tool loop to tolerate a failed fetch.** Better engineering, and
   still worth doing, but on its own it would leave every search spending a wasted round trip on a
   source that cannot be read.
2. **Exclude OHLQ only for the Ollama and LiteLLM providers.** Rejected: it would make the pricing
   evidence base depend on the configured provider, which is exactly the divergence ADR 0003 warns
   against.
3. **Fetch OHLQ directly from the application rather than through Ollama Cloud.** Rejected here: it
   adds a bespoke scraper and an SSRF/robots surface the invariants deliberately constrain, for one
   site.

## Supersession Criteria

Superseded if OHLQ becomes retrievable and is restored as a preferred source, if pricing moves off
grounded web search entirely (plan.md P2-03), or if per-provider source lists are ever adopted.

## Cross-links

- [ADR 0002: Local-First Pricing Catalog](0002-local-first-pricing-catalog.md)
- [ADR 0006: LiteLLM Gateway Provider](0006-litellm-gateway-provider.md)
