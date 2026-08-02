---
name: bourbonbook-invariants
description: "The single source of truth for Bourbon Book's architectural and security invariants — the constraints every agent designs, implements, reviews, and validates against. Read this before proposing, writing, or reviewing any change. Overturning an invariant requires a new ADR that names the consequence, never a judgment call in a single change."
---

## Why this exists

These constraints were previously restated in every agent definition, which meant five copies that
could drift apart — and a critic enforcing different rules than a reviewer is worse than no critic.
This file is the one copy. Agents cite it; they do not re-derive it.

Each invariant names the ADR or Confirmed Decision that established it. If a change contradicts one,
that is not automatically wrong — but it requires a new ADR with `Status: Proposed` that states the
consequence of overturning it, and Aaron's decision. It is never something to resolve inside a work
item.

## Runtime and deployment

1. **One Docker container on a single Unraid host.** Not multi-tenant, not clustered, not a SaaS
   product. `MAX_USERS` caps the user count (default 10). — ADR 0001
2. **One Uvicorn worker, one SQLite writer.** Any design implying concurrent writers, multiple
   workers, or a process outside the app process violates this. Background work runs in the
   lifespan-owned worker inside that single process. Redis, Celery, RQ, and extra replicas are out
   of scope while SQLite is the write store. — ADR 0001, Confirmed Decisions 11 and 15
3. **All durable state under `/data`.** The database, uploads, avatars, and the managed `.env`
   override live there. Nothing durable belongs in the image.
4. **The container runs non-root**, logs to stdout/stderr, and exposes `/healthz` and `/readyz`.
   Migration bootstrap runs before Uvicorn serves.

## Data

5. **SQLite is the source of truth.** Every acceleration structure — Qdrant above all — is a
   rebuildable index that must degrade safely when absent, stale, or wrong. The app must remain
   fully usable with `QDRANT_URL` unset. — ADR 0002
6. **Migrations are forward-only, idempotent, and SQLite-compatible**, and must work on a fresh
   database, a recognized pre-Alembic legacy database, and an already-versioned database. Exactly
   one Alembic head. — plan.md Cross-Cutting Requirements
7. **A new NOT NULL column needs a `server_default`.** SQLite cannot alter a column in place; use
   `op.batch_alter_table`. Never reference ORM models from a revision.

## AI providers and pricing

8. **Local-first before any network call.** Pricing resolves exact SQLite catalog match → optional
   Qdrant fuzzy match at the same size → only then a grounded web search. — ADR 0002
9. **The catalog outranks the model.** A verified catalog match short-circuits further model calls.
   Model output is a proposal, never a persisted fact; normalization and validation happen in
   application code. Structured Outputs constrain shape, not truth.
10. **Both provider branches always have a defined answer.** A path that works only when
    `ANALYSIS_PROVIDER=openai` is a regression — the direction is local-first. — ADR 0003
11. **Degrade, never fail hard.** A provider timeout, 5xx, malformed response, or unreachable host
    must fall back to the manual path: the photo is still saved and the review form still opens.
    Never a 500, never data loss, never a silently empty bottle.
12. **Prices need provenance.** Require exact product/release/edition/**size** matching, store the
    source URL and observation date, and keep MSRP, retailer asking price, completed sale, auction
    result, and user-reported price as distinct evidence types. Return unavailable rather than
    inventing a price. — Confirmed Decisions, ADR 0002
13. **Model roles are fixed configuration, not a benchmark gate.** Do not reintroduce a
    benchmark-gated model selection. — ADR 0003

## Security and privacy

14. **Every authenticated browser route enforces CSRF and owner scoping.** Admin routes stay behind
    the admin boundary. A route that reads or writes a bottle must scope it to the owner.
15. **Nothing sensitive reaches logs, metrics, or the usage ledger.** No prompts, responses, bottle
    names, emails, URLs, API keys, cookies, tokens, or full page contents. Error types are bounded
    to 40 characters precisely so exception text cannot leak.
16. **Treat URLs and content from users, models, and the web as hostile.** Validate scheme and host,
    block loopback/private/link-local destinations, cap redirects and response size, prevent DNS
    rebinding and SSRF. Respect `robots.txt` and site terms; never bypass a paywall or access
    control.
17. **`.env` is real and never edited by an agent.** Only `.env.example` gets new keys, with
    placeholder values. Secrets are never displayed in `/admin/config`.

## Frontend

18. **Server-rendered Jinja, no build step, no SPA framework, no CDN dependency.** One stylesheet,
    one script, two breakpoints (`max-width:900px`, `max-width:620px`). — ADR 0001
19. **The service worker caches static assets only** — never HTML, never authenticated responses.
    Changing a shell asset requires bumping the `CACHE` version.
20. **Accessibility decisions are product decisions.** The Atkinson Hyperlegible font on form
    controls, 44px minimum touch targets, visible focus indicators, the skip link, and the
    `prefers-reduced-motion` block are not incidental styling. — Confirmed Decision 2

## Testing

21. **Deterministic tests never touch the network.** No OpenAI, Ollama, Qdrant, SMTP, or web calls —
    injected fakes and captured fixtures only. Tests use `tmp_path` SQLite, never `/data`.
22. **Never weaken a gate to pass it.** Not the 80% coverage floor, not a lint rule, not a security
    check, not by skipping a test or adding a blanket `# noqa` / `# nosec`.

## Process

23. **The as-built invariant.** `docs/architecture/` describes only what is checked in today.
    Proposed work lives in `docs/adr/plan.md` and in `Proposed` ADRs.
24. **ADRs are immutable.** Supersede; never edit or delete an accepted decision.
25. **No agent merges, deploys, or writes to production.** A merge to the default branch triggers
    `docker-publish.yml` and tags a release.
26. **Halt on ambiguity.** An agent that hits an unclear situation escalates rather than inventing a
    decision. Escalating is a successful outcome.
