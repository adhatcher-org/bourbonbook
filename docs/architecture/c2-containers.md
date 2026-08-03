# C2 Containers

Rendered SVG: [c2-containers.svg](diagrams/c2-containers.svg)  
Baseline ADR: [ADR 0001](../adr/0001-current-architecture-baseline.md)
Pricing-catalog ADR: [ADR 0002](../adr/0002-local-first-pricing-catalog.md)

This view shows the deployed containers and the persistent storage boundary that the current
application relies on.

```mermaid
flowchart LR
  browser[Browser / installed PWA]
  swag[SWAG / reverse proxy]

  subgraph docker["Unraid Docker host"]
    app["FastAPI / Uvicorn app container (single worker)<br/>+ in-process catalog-import worker task"]

    subgraph data["Persistent /data volume"]
      sqlite[(SQLite database - users, bottles, tokens, api_usage,<br/>catalog_prices, catalog_import_batches, catalog_import_proposals)]
      uploads[(Normalized bottle photos + avatars)]
      imports[(Staged catalog-import source files)]
      config[(Managed .env configuration)]
      logs[(JSON log files)]
    end
  end

  ollama[Ollama - self-hosted]
  ollama_cloud[Ollama Cloud web_search / web_fetch]
  openai[OpenAI]
  qdrant[(Qdrant - optional sidecar/service)]
  smtp[SMTP relay]
  prometheus[Prometheus]
  promtail[Promtail]
  loki[(Loki)]

  browser --> swag --> app

  app --> sqlite
  app --> uploads
  app --> imports
  app --> config
  app --> logs

  app --> ollama
  app --> ollama_cloud
  app --> openai
  app --> qdrant
  app --> smtp

  prometheus --> app
  logs --> promtail --> loki
```

## Notes

- The browser and installed PWA are the user-facing client.
- One FastAPI/Uvicorn container runs the entire app, always as a single worker — session state,
  rate limiting, the catalog-import worker's single extraction lane, and the in-process
  GPU/model-residency assumptions documented in `plan.md` are all process-local and would fragment
  across multiple workers/replicas. The catalog-import worker is an `asyncio` task started and
  stopped by the FastAPI lifespan, **not** a separate container or process.
- `/data` contains all durable state and should be mounted from Unraid storage; the container
  filesystem itself is disposable.
- SQLite is the single source of truth for every table, including the shared `catalog_prices` MSRP
  cache and the `catalog_import_batches`/`catalog_import_proposals` staging tables; uploads (bottle
  photos + user avatars), managed config, and JSON logs sit beside it under `/data`.
- `<DATA_DIR>/catalog-imports/<batch_id>/` holds staged import source files at `0600`, written into
  a `0700` temp directory and atomically renamed into place. They are deleted once a batch reaches
  review, and expired terminal/orphan directories are swept on a TTL
  (`CATALOG_IMPORT_SOURCE_EXPIRY_HOURS`, default 24) by the worker's poll loop and at startup —
  queued and extracting batches keep their input regardless of age.
- Qdrant is deployed as a separate service/container reachable over the internal Docker network
  (never exposed through the public SWAG route). It is optional infrastructure: every Qdrant call in
  the app degrades to a no-op on timeout/HTTP error, and the whole collection can be rebuilt from
  SQLite at any time via `make price-catalog-reindex`.
- External services (self-hosted Ollama, Ollama Cloud, OpenAI, Qdrant, SMTP,
  Prometheus/Promtail/Loki) stay outside the app container boundary. Ollama Cloud is reached over
  the public internet and is only contacted when `ANALYSIS_PROVIDER=ollama` and `OLLAMA_API_KEY` is
  set — it is not part of the self-hosted Ollama deployment.

## Cross-links

- [C1 System Context](c1-system-context.md)
- [C3 Components](c3-components.md)
- [C4 Code](c4-code.md)
- [Rendered SVG](diagrams/c2-containers.svg)
