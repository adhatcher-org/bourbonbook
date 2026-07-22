# Component Design: Administration & Configuration

Modules: `bourbonbook/admin_config.py`, admin routes in `bourbonbook/main.py`,
`bourbonbook/admin_cli.py`
Related: [HLDD](../hldd.md) · [Identity & sessions](identity-and-sessions.md) ·
[Observability & operations](observability-and-operations.md)

## Responsibility

Give an administrator (`User.is_admin`) full operational control of the deployment without shell
access: manage users, monitor AI/API usage, curate the shared price catalog, and change any
environment-driven setting — all gated behind `auth.require_admin()` and audited via structured
`admin_action` log events.

## Admin routes

| Route | Purpose |
| --- | --- |
| `GET /admin/users`, `GET /admin/users/{id}` | Paginated/searchable user list and detail |
| `POST /admin/users/{id}/send-reset`, `/resend-verification` | Admin-triggered identity emails, rate-limited |
| `POST /admin/users/{id}/email` | Correct a user's email after out-of-band verification (requires typed confirmation match) |
| `GET /admin/catalog`, `POST /admin/catalog` | Browse/search/sort `CatalogPrice`; bulk edit or delete |
| `GET /admin/catalog-import`, `POST /admin/catalog-import` | Upload PNG/JPEG/PDF price sheets — currently validates only; see [Pricing & catalog](pricing-and-catalog.md) known gap |
| `GET /admin/config`, `POST /admin/config` | View/edit the managed configuration file |
| `POST /admin/restart` | Self-`SIGTERM`, relies on the container's process supervisor to come back up |
| `GET /admin/usage` | AI/API usage dashboard, aggregated + paginated recent events |

## Managed configuration (`admin_config.py`)

- **Field registry**: `CONFIG_FIELDS`, a tuple of typed `ConfigField` entries (one per managed
  `Settings` attribute, grouped by Application/Analysis/Pricing/Email/Bootstrap/Network/
  Security/Observability) — the single source both the admin UI and validation walk. This is what
  makes "every setting listed in `.env.example` is admin-editable" true by construction rather than
  by two documents staying manually in sync.
- **Validation** (`parse_config_form()`): per-field type/range/allowed-value checks (`boolean`,
  `choice`, `integer` with min/max, `url` requiring an http(s) scheme + netloc, `email`), plus
  hardcoded extra rules (`SESSION_SECRET` ≥32 chars, `DEFAULT_ADMIN_PASSWORD` if given ≥10 chars).
  Builds a candidate `Settings` object and re-runs `Settings.validate_identity()` plus an extra check
  that `OPENAI_API_KEY` is set when `ANALYSIS_PROVIDER == "openai"`. All errors are collected and
  raised together, not fail-fast on the first one.
- **Secret handling**: a blank submitted value preserves the current stored value; only an explicit
  `clear_<KEY>` checkbox actually clears a secret. Secrets are never rendered back into the form.
- **Atomic write** (`write_managed_config()`): builds a `.env`-style file (one `KEY=json.dumps(value)`
  line per registered field, in declared order), writes to a `.tmp` sibling, `chmod(0o600)`, then
  `Path.replace()` — an atomic rename onto `<DATA_DIR>/.env`.
- **Precedence**: `Settings.from_env()` merges `{**os.environ, **load_managed_overrides()}` — the
  managed file's values win over container/OS environment variables. This is the mechanism behind
  "admin-managed configuration takes precedence over container environment values."
- **Restart flow**: `POST /admin/restart` returns an HTML page with a 5-second meta-refresh back to
  `/admin/config`, then (via a `BackgroundTask`) calls `app.state.restart` —
  `os.kill(os.getpid(), SIGTERM)`. Because `entrypoint.py` `os.execvp`'d into uvicorn (same PID),
  this is a real process termination, **not** a live reload. The container's `restart:
  unless-stopped` policy (or an equivalent operator-managed supervisor) is what actually brings the
  process back up with the freshly written config — the app has no built-in respawn.

## Catalog administration

`/admin/catalog` gives an admin a paginated, sortable, searchable view over `CatalogPrice` with
inline name/price edits and bulk deletion — a manual escape hatch for correcting a bad Tier 3 (OpenAI)
result or a stale entry without waiting for the 90-day TTL. See
[Pricing & catalog](pricing-and-catalog.md) for how those rows are otherwise populated.

## Usage dashboard

`/admin/usage` reads the `ApiUsage` ledger (see
[Observability & operations](observability-and-operations.md)) and presents aggregated
totals by provider/operation/model/success plus a paginated recent-events table, filterable by a
`days` window — the primary operator-facing view into AI cost/reliability without needing a
Prometheus/Grafana stack running.

## Sole-admin recovery (`admin_cli.py`)

Covered in depth in [Identity & sessions](identity-and-sessions.md); included here because it is the
break-glass counterpart to the web-based admin tools when the only admin account is locked out and
the web UI itself is unreachable.

## Design properties worth preserving

- `CONFIG_FIELDS` as the single registry is a load-bearing convention: adding a new `Settings`
  field without adding a matching `ConfigField` entry means it silently becomes un-editable from the
  admin UI (not a validation failure — just invisible), so this should be part of any PR checklist
  for new configuration.
- The restart action being a real process exit (not a soft reload) means production deployments
  **must** run under a supervisor; the app deliberately does not try to be its own supervisor. See
  HLDD §7.3 and README's "Deployment Validation Runbook."
