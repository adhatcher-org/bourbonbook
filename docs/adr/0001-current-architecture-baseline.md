# ADR 0001: Current Architecture Baseline

Status: Accepted  
Date: 2026-06-30

This ADR records the architecture that exists in the checked-out Bourbon Book codebase today. It is
intended as a baseline record, not as a roadmap document. Planned RAG/Qdrant work remains out of
scope here and is not depicted as current architecture.

Related architecture views:

- [C1 System Context](../architecture/c1-system-context.md)
- [C2 Containers](../architecture/c2-containers.md)
- [C3 Components](../architecture/c3-components.md)
- [C4 Code](../architecture/c4-code.md)

## Context

Bourbon Book is a private, server-rendered FastAPI application that runs as a single Python web
process inside Docker on Unraid. It serves a browser-based, installable PWA experience for
collecting bourbon bottles, managing verified identities, and reviewing AI-assisted bottle
analysis.

The application has to work in a home-lab deployment with durable state on `/data`, limited process
count, and operator-managed configuration. That combination means the architecture must favor
clarity, restartable configuration, and observable local state over horizontally scaled services.

## Decision

The following decisions have been made and are part of the current baseline:

1. The UI is server-rendered with FastAPI, Jinja templates, and a lightweight PWA shell. The app is
   not a client-side SPA.
2. The production deployment is a single-process Docker container on Unraid, fronted by SWAG or an
   equivalent reverse proxy.
3. Persistence uses SQLite through SQLAlchemy, with Alembic handling schema bootstrap and upgrades.
4. Durable state lives under `/data`, including the SQLite database, normalized uploads, managed
   configuration, and logs.
5. Authentication uses signed sessions, CSRF protection, verified email identities, and a bootstrap
   admin flow for first run recovery.
6. Bottle analysis can use either Ollama or OpenAI. If the selected provider is unavailable, the
   app falls back to manual review instead of blocking the bottle workflow.
7. Price research is OpenAI-only and must be grounded in URLs actually consulted by the model.
8. Email delivery is capture-only in development and SMTP in production.
9. Environment configuration and admin-managed configuration are restart-driven, not live-reloaded.
10. Observability includes Prometheus metrics, structured logs, health checks, and a local API usage
    ledger stored in SQLite.

## Rationale

These choices keep the app simple enough to operate in a personal Unraid environment while still
supporting the key product behaviors:

- Photos can be normalized and stored locally without a separate media service.
- Authentication and admin workflows stay in one process and one session model.
- SQLite is a good fit for a single-writer deployment that stores everything on `/data`.
- Restart-based config avoids partial state and makes Unraid operations predictable.
- Local observability gives the operator a useful picture of provider usage, request health, and
  delivery behavior without adding an external telemetry stack requirement.

## Consequences

- The app intentionally trades horizontal scaling for straightforward deployment and maintenance.
- One worker is the safe default because session state, rate limits, and other runtime protections
  are process-local.
- Persistent state must be backed up from `/data`; the container filesystem is disposable.
- Admin changes to config require a restart to take effect, which is deliberate and documented.
- OpenAI features degrade cleanly when the API key or provider is unavailable, but the app should
  still remain usable for manual entry and review.
- RAG/Qdrant-related work can be added later, but it must be introduced by a separate ADR because it
  would change the persistence and retrieval model materially.

## Alternatives Considered

1. A SPA frontend with a separate JSON API.
   Rejected because it would add client complexity without improving the core bottle workflow.
2. PostgreSQL plus a multi-process or multi-replica deployment.
   Rejected for the current baseline because it would increase operational cost and complexity for a
   single-user home-lab deployment.
3. Live-reloaded configuration or a shared config service.
   Rejected because restart-based config is simpler and more reliable in the Unraid environment.
4. Always-on OpenAI analysis.
   Rejected because the app needs to remain usable with a local Ollama provider and a manual
   fallback path.
5. Depicting the roadmap RAG/Qdrant work as current architecture.
   Rejected because those capabilities are not part of the checked-out implementation.

## Operational Constraints

- The app must run correctly with all durable data mounted at `/data`.
- The deployment should expose only the application port inside Docker and let the reverse proxy own
  public ingress.
- Production settings require secure cookies, HTTPS public URLs, and restricted proxy headers.
- The operator can manage configuration through `/admin/config`, but saved values only take effect
  after restart.
- Log aggregation should read the JSON log file under `/data/logs` or the container stdout stream.
- Metrics should be scraped directly from the app, not through the public reverse proxy.

## Supersession Criteria

This ADR can be narrowed or partially superseded by future ADRs if the app changes in one of these
areas:

- switching away from the current server-rendered FastAPI/Jinja approach,
- changing the single-process Docker/Unraid deployment model,
- replacing SQLite or Alembic bootstrap,
- altering the authentication, CSRF, or bootstrap-admin model,
- changing analysis or pricing provider behavior,
- replacing the logging, metrics, or usage-recording approach,
- introducing a production RAG/Qdrant subsystem.

Future ADRs should supersede only the affected decision slice when possible, rather than replacing
the whole baseline.
