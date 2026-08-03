# C1 System Context

Rendered SVG: [c1-system-context.svg](diagrams/c1-system-context.svg)  
Baseline ADR: [ADR 0001](../adr/0001-current-architecture-baseline.md)
Pricing-catalog ADR: [ADR 0002](../adr/0002-local-first-pricing-catalog.md)

This context view shows the people and external systems Bourbon Book currently interacts with. It
includes the shipped local-first pricing/catalog subsystem (SQLite catalog cache + optional
sparse-vector Qdrant fuzzy match). It does **not** include the larger, unimplemented Phase 2 RAG
roadmap (dense-embedding evidence pipeline, governed source registry, scheduled crawling/discovery)
tracked in `docs/adr/plan.md` — those remain future work, not current architecture.

```mermaid
flowchart LR
  user[Collection user]
  admin[Administrator]
  browser[Browser / installed PWA]

  subgraph bourbon["Bourbon Book"]
    app[Bourbon Book web app]
  end

  swag[SWAG / reverse proxy]
  ollama[Ollama - self-hosted vision/text]
  ollama_cloud[Ollama Cloud web_search / web_fetch]
  openai[OpenAI analysis + web search]
  qdrant[(Qdrant - optional)]
  smtp[SMTP relay]
  prometheus[Prometheus]
  promtail[Promtail]
  loki[(Loki)]

  user --> browser
  admin --> browser
  browser --> swag --> app

  app --> ollama
  app --> ollama_cloud
  app --> openai
  app --> qdrant
  app --> smtp

  prometheus --> app
  app --> promtail --> loki
```

## Notes

- Collection users and administrators both reach the app through a browser or installed PWA.
- SWAG or an equivalent reverse proxy terminates public HTTPS and forwards requests to the app.
- Ollama (self-hosted, `OLLAMA_URL`) is the local vision/text-analysis provider. It also powers
  catalog price-sheet extraction, both in-app via the admin catalog-import worker and offline via
  `make price-catalog-extract-screenshots` — the same `catalog_extract.py` code either way.
- **Ollama Cloud** (`https://ollama.com`) is a distinct external system, used only by
  `ollama_search.py`: when `ANALYSIS_PROVIDER=ollama`, the model's `web_search`/`web_fetch` tool
  calls are executed against Ollama Cloud's HTTP API with `OLLAMA_API_KEY`. Without that key,
  Ollama-provider price search returns `unavailable` rather than falling through to OpenAI.
- OpenAI is used for grounded bottle analysis and price research when `ANALYSIS_PROVIDER=openai`.
  Grounded price search — from either provider — only runs when the local catalog and Qdrant have
  no acceptable match.
- Qdrant is an **optional** local-hash sparse-vector index (`QDRANT_URL` unset disables it
  entirely). It accelerates fuzzy product-name matching against the SQLite `catalog_prices` table;
  SQLite remains the source of truth and Qdrant is fully rebuildable from it (`make
  price-catalog-reindex`). See [ADR 0002](../adr/0002-local-first-pricing-catalog.md).
- SMTP is used for production email delivery, while development captures messages locally.
- Prometheus scrapes the app directly.
- Promtail tails the app logs and forwards them to Loki.

## Cross-links

- [C2 Containers](c2-containers.md)
- [C3 Components](c3-components.md)
- [C4 Code](c4-code.md)
- [Rendered SVG](diagrams/c1-system-context.svg)
