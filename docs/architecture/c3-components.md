# C3 Components

Rendered SVG: [c3-components.svg](diagrams/c3-components.svg)  
Baseline ADR: [ADR 0001](../adr/0001-current-architecture-baseline.md)

This view breaks the app container into the main runtime components that support the current
workflow.

```mermaid
flowchart LR
  browser[Browser / installed PWA]

  subgraph app["FastAPI / Uvicorn app"]
    subgraph bootstrap["Bootstrap and runtime"]
      entrypoint[entrypoint.py]
    end

    subgraph presentation["Presentation and routing"]
      routes[main.py routes]
      templates[Jinja templates]
      static[Static PWA assets]
    end

    subgraph identity["Identity and sessions"]
      auth[auth.py]
      identity_mod[identity.py]
      tokens[tokens.py]
    end

    subgraph bottles["Bottle workflow"]
      bottle_routes[Bottle routes]
      photos[photos.py]
      catalog[catalog.py]
      analysis[analysis.py]
    end

    subgraph ai["AI orchestration and providers"]
      ollama_mod[ollama.py]
      openai_mod[openai_provider.py]
    end

    subgraph admin["Administration and configuration"]
      admin_routes[Admin routes]
      admin_config[admin_config.py]
    end

    subgraph persistence["Persistence and migrations"]
      database[database.py]
      models[models.py]
      migrations[migrations.py]
    end

    subgraph obs["Observability and runtime guards"]
      observability[observability.py]
      logging_config[logging_config.py]
    end
  end

  sqlite[(SQLite / Alembic)]
  uploads[(Uploads in /data)]
  config[(Managed config in /data)]
  logs[(Logs in /data)]
  ollama[Ollama]
  openai[OpenAI web search]
  prometheus[Prometheus]
  loki[(Loki)]

  browser --> routes
  routes --> templates
  routes --> static
  routes --> auth
  routes --> identity_mod
  routes --> tokens
  routes --> bottle_routes
  routes --> admin_routes
  routes --> observability

  bottle_routes --> photos
  bottle_routes --> catalog
  bottle_routes --> analysis
  analysis --> ollama_mod
  analysis --> openai_mod
  ollama_mod --> ollama
  openai_mod --> openai

  admin_routes --> admin_config
  admin_config --> config

  database --> sqlite
  migrations --> database
  bottle_routes --> database
  identity_mod --> database
  auth --> database

  observability --> prometheus
  logging_config --> logs
  logging_config --> loki
  photos --> uploads
  entrypoint --> logging_config
  entrypoint --> migrations
```

## Notes

- `main.py` owns the app assembly and route registration.
- `auth.py`, `identity.py`, and `tokens.py` implement the verified-session model.
- Bottle handling spans routes, photo normalization, catalog lookup, and analysis provider dispatch.
- `admin_config.py` handles the restart-driven managed configuration file under `/data`.
- `database.py`, `models.py`, and `migrations.py` form the persistence layer.
- `observability.py` and `logging_config.py` handle metrics, usage recording, and log output.

## Cross-links

- [C1 System Context](c1-system-context.md)
- [C2 Containers](c2-containers.md)
- [C4 Code](c4-code.md)
- [Rendered SVG](diagrams/c3-components.svg)
