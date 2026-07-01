# Bourbon Book

A private, mobile-first bourbon collection that photographs bottles, asks a local Ollama vision model to read the label, and keeps the result in an editable personal catalog. It is an installable web app sized for iPhone and desktop browsers.

## Run locally

```bash
cp .env.example .env
# Set SESSION_SECRET in .env
uv sync
uv run --env-file .env uvicorn bourbonbook.main:app --reload
```

Open `http://localhost:8000`, create an account, and add a bottle. Local development uses `https://ollama.aaronhatcher.com` by default. If the selected analyzer is not reachable, the photo is still saved and the review form opens for manual entry.

Form input and select values now use a self-hosted Atkinson Hyperlegible Next font for improved readability. The WOFF2 assets are stored under `bourbonbook/static/fonts/` with a local attribution note.

Development defaults to captured email delivery. Verification and reset messages are retained only
in the running process. To exercise real delivery, set `EMAIL_DELIVERY_MODE=smtp` and configure the
SMTP settings shown in `.env.example`. Links are always built from `PUBLIC_BASE_URL`, never the
incoming Host header.

Application startup runs the idempotent migration bootstrap before serving requests. It initializes
a fresh database, safely stamps a recognized pre-Alembic database, and upgrades an already-versioned
database to the latest revision. Container startup also runs it explicitly before Uvicorn.

Choose the image-analysis provider in `.env` and restart the app:

```dotenv
# Local Ollama (default)
ANALYSIS_PROVIDER=ollama

# Or OpenAI
ANALYSIS_PROVIDER=openai
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-5.5
```

Keep the real API key only in `.env` or your container's secret environment settings; do not add it to `.env.example` or commit it.

When OpenAI is selected, bottle analysis is followed by a grounded web search for current MSRP and
secondary-market evidence. Only prices tied to a URL actually consulted by OpenAI are accepted.
The bottle detail page shows the source and lookup basis, and the edit page can refresh prices
without re-analyzing the photo. Each refresh uses an additional OpenAI web-search tool call.

Admins can open `/admin/users` to search users, correct an email address after out-of-band identity
verification, and send verification or reset links. `/admin/usage` shows recent OpenAI/Ollama call
counts, token-like counts, failures, and durations from the local usage ledger. The ledger stores
provider, operation, model, bounded error type, duration, token counts, optional internal user ID,
and timestamp only; it does not store prompts, responses, bottle names, email addresses, URLs, or
API keys. Set `API_USAGE_RETENTION_DAYS` to control local ledger cleanup.

`/admin/config` exposes every setting listed in `.env.example` with server-side type, range, and
allowed-value validation. Secret fields are never displayed; leave one blank to preserve it or use
its clear checkbox for optional secrets. Saves are written atomically with owner-only permissions
to `<DATA_DIR>/.env`, which takes precedence over container environment values at the next startup.
The restart action terminates the app process after returning its response. Production deployments
must use a process supervisor such as Docker with `restart: unless-stopped`; without one, the app
stops and must be started manually.

## Docker / Unraid

The production image is `ghcr.io/adhatcher-org/bourbonbook:latest`. It listens on container port
`8000`, stores all persistent state under `/data`, and runs one Uvicorn worker. Logs are mirrored to
stdout/stderr and written as newline-delimited JSON to `/data/logs/bourbonbook.log`. The checked-in
`compose.yaml` is a local-development smoke-test topology: it publishes
host port `8088`, uses a named volume, and creates an example `bourbon-services` network. Do not copy
those local defaults into Unraid production.

For local Docker testing only:

```bash
cp .env.example .env
docker network create bourbon-services  # once, if it does not already exist
docker network connect bourbon-services ollama  # once, for an existing Ollama container
docker compose up -d --build
```

### Production Unraid Settings

Create an Unraid path setting named `DATA_PATH` and map its host value to container path `/data`.
`/mnt/user/appdata/bourbonbook` is a reasonable example host value, but backups and restores must use
the value actually configured in Unraid.

| Setting name | Unraid type | Container target/key | Example/default | Required | Secret |
| --- | --- | --- | --- | --- | --- |
| Repository | Repository | Image | `ghcr.io/adhatcher-org/bourbonbook:latest` | Yes | No |
| Web UI | WebUI | URL | `https://bourbonbook.aaronhatcher.com` | Yes | No |
| App port | Port | Container `8000` | No host-published port | Yes | No |
| `DATA_PATH` | Path | `/data` | `/mnt/user/appdata/bourbonbook` | Yes | No |
| Docker network | Network | Unraid-selected network | SWAG shared network | Yes | No |
| Optional service network | Network | Additional network | Ollama/service network | If using Ollama | No |
| `APP_ENV` | Variable | `APP_ENV` | `production` | Yes | No |
| `SESSION_SECRET` | Variable | `SESSION_SECRET` | generated with `openssl rand -hex 32` | Yes | Yes |
| `SECURE_COOKIES` | Variable | `SECURE_COOKIES` | `true` | Yes | No |
| `PUBLIC_BASE_URL` | Variable | `PUBLIC_BASE_URL` | `https://bourbonbook.aaronhatcher.com` | Yes | No |
| `PROXY_HEADERS` | Variable | `PROXY_HEADERS` | `true` | Yes | No |
| `FORWARDED_ALLOW_IPS` | Variable | `FORWARDED_ALLOW_IPS` | SWAG fixed IP or smallest proxy CIDR | Yes | No |
| `ANALYSIS_PROVIDER` | Variable | `ANALYSIS_PROVIDER` | `ollama` or `openai` | Yes | No |
| `OLLAMA_URL` | Variable | `OLLAMA_URL` | `http://ollama:11434` | If using Ollama | No |
| `OLLAMA_MODEL` | Variable | `OLLAMA_MODEL` | `gemma3:4b` | If using Ollama | No |
| `OPENAI_API_KEY` | Variable | `OPENAI_API_KEY` | masked value | If using OpenAI | Yes |
| `OPENAI_MODEL` | Variable | `OPENAI_MODEL` | `gpt-5.5` | No | No |
| `EMAIL_DELIVERY_MODE` | Variable | `EMAIL_DELIVERY_MODE` | `smtp` | Yes | No |
| `SMTP_HOST` | Variable | `SMTP_HOST` | relay hostname | Yes for SMTP | No |
| `SMTP_PORT` | Variable | `SMTP_PORT` | `587` | Yes for SMTP | No |
| `SMTP_USERNAME` | Variable | `SMTP_USERNAME` | relay username | Relay-dependent | Yes |
| `SMTP_PASSWORD` | Variable | `SMTP_PASSWORD` | masked value | Relay-dependent | Yes |
| `SMTP_FROM_EMAIL` | Variable | `SMTP_FROM_EMAIL` | `bourbonbook@example.com` | Yes for SMTP | No |
| `SMTP_FROM_NAME` | Variable | `SMTP_FROM_NAME` | `Bourbon Book` | No | No |
| `SMTP_TLS_MODE` | Variable | `SMTP_TLS_MODE` | `starttls` | Yes for SMTP | No |
| `VERIFICATION_TTL_HOURS` | Variable | `VERIFICATION_TTL_HOURS` | `24` | No | No |
| `RESET_TTL_MINUTES` | Variable | `RESET_TTL_MINUTES` | `60` | No | No |
| `DEFAULT_ADMIN_EMAIL` | Variable | `DEFAULT_ADMIN_EMAIL` | owner email | First startup only | No |
| `DEFAULT_ADMIN_PASSWORD` | Variable | `DEFAULT_ADMIN_PASSWORD` | masked temporary value | First startup only | Yes |
| `METRICS_ENABLED` | Variable | `METRICS_ENABLED` | `true` | No | No |
| `API_USAGE_RETENTION_DAYS` | Variable | `API_USAGE_RETENTION_DAYS` | `90` | No | No |
| `LOG_FORMAT` | Variable | `LOG_FORMAT` | `json` | Yes | No |
| `LOG_LEVEL` | Variable | `LOG_LEVEL` | `INFO` | No | No |

Never put real secrets in the image, Compose file, documentation examples, or repository. Use masked
Unraid variables for `SESSION_SECRET`, `OPENAI_API_KEY`, SMTP credentials, and the temporary
bootstrap password. Production startup rejects insecure cookies, non-HTTPS `PUBLIC_BASE_URL`, missing
proxy-header support, an empty forwarded allowlist, and any `*` entry in `FORWARDED_ALLOW_IPS`.

The initial admin is bootstrap-only. Set `DEFAULT_ADMIN_EMAIL` and masked `DEFAULT_ADMIN_PASSWORD`
for the first start, verify that the account was created and received a verification email, then
remove `DEFAULT_ADMIN_PASSWORD` from the Unraid template and restart the container. Startup must
still succeed after removal because an admin already exists. If restoring an empty or pre-admin
database, supply fresh bootstrap values again.

Keep the container at one Uvicorn worker. Login, registration, verification, and reset rate limits
are process-local; add a shared limiter before scaling workers or replicas.

The container health check calls `/healthz`, which reports only process liveness. `/readyz` verifies
database connectivity and that Alembic has reached the application migration head.

### Prometheus, SWAG, and Loki

Prometheus should scrape Bourbon Book directly over an internal Docker network, not through the
public HTTPS host. Example scrape job:

```yaml
scrape_configs:
  - job_name: bourbonbook
    static_configs:
      - targets: ["bourbonbook:8000"]
```

If Prometheus is not on the same network as SWAG/Bourbon Book, attach both containers to a dedicated
internal monitoring network. Keep `/metrics`, `/healthz`, and `/readyz` off the public SWAG virtual
host with exact-match denies, for example:

```nginx
location / {
    proxy_pass http://bourbonbook:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;
}

location = /metrics { return 404; }
location = /healthz { return 404; }
location = /readyz { return 404; }
```

Replace `bourbonbook` with the actual Docker DNS name if Unraid assigns a different container name.
Use the same SWAG HTTPS route for LAN and Tailscale browser testing; do not publish the application
port on the host for direct browser access. Preserve or replace forwarding headers at SWAG so the app
trusts only SWAG's fixed address or proxy-network CIDR, never arbitrary client-supplied forwarding
headers.

Useful starter PromQL:

```promql
sum(rate(bourbonbook_auth_events_total{event="login",result="failure"}[5m]))
sum(rate(bourbonbook_http_requests_total{status_class="5xx"}[5m]))
sum(rate(bourbonbook_ai_tokens_total{provider="openai"}[5m])) by (operation, direction)
histogram_quantile(0.95, sum(rate(bourbonbook_ai_request_duration_seconds_bucket[5m])) by (le, provider, operation))
sum(rate(bourbonbook_ai_requests_total{result="failure"}[5m])) by (provider, operation)
```

For Promtail/Loki, mount the configured Unraid `DATA_PATH` read-only into the collector and scrape
`<DATA_PATH>/logs/*.log` (inside the app container this is `/data/logs/*.log`). Each line is JSON
regardless of the console `LOG_FORMAT`. Keep low-cardinality labels such as `app`, `container`,
`level`, and optionally `event`; leave request IDs and user IDs as parsed fields, not labels. A
minimal Promtail scrape target is:

```yaml
scrape_configs:
  - job_name: bourbonbook
    static_configs:
      - targets: [localhost]
        labels:
          app: bourbonbook
          __path__: /bourbonbook-data/logs/*.log
    pipeline_stages:
      - json:
          expressions:
            level: severity
            event: event
```

In this example, mount the same Unraid appdata directory at `/bourbonbook-data` in Promtail. Use
external `logrotate` with `copytruncate` or normal rename/create rotation; the app's watched file
handler reopens a replaced file. Useful Loki filters include:

```logql
{app="bourbonbook"} | json | event="login_failed"
{app="bourbonbook"} | json | event="admin_action"
{app="bourbonbook"} | json | event="ai_request_completed" | error_type!=""
{app="bourbonbook"} | json | request_id="paste-request-id"
```

For interactive recovery of the sole administrator, open a container terminal and run
`uv run python -m bourbonbook.admin_cli recover`. It prompts for secrets and does not accept a
password argument that could leak through shell history or the process list.

### Deployment Validation Runbook

Before deploying a migration-enabled image, stop the old Bourbon Book container and copy or snapshot
the complete Unraid host directory configured by `DATA_PATH`, including `bourbonbook.db` and
`uploads/`. Do not make a normal file copy of `bourbonbook.db` while the app is running. Keep the
backup until the upgraded container has started and the catalog, ownership, price sources, and photos
have been checked.

Production rollout checklist:

1. Pull or build the target image and confirm the Unraid template uses the production settings above.
2. Start the container and inspect startup logs for migration bootstrap, admin bootstrap if needed,
   and Uvicorn startup. A partial or unknown unversioned schema intentionally fails startup.
3. Verify internal health from another container on the selected Docker network:
   `curl http://bourbonbook:8000/healthz` and `curl http://bourbonbook:8000/readyz`.
4. Verify public routing through SWAG at `https://bourbonbook.aaronhatcher.com`, including secure
   cookies, redirects, PWA assets, and normal browser access from LAN or Tailscale.
5. Confirm public `https://bourbonbook.aaronhatcher.com/metrics`, `/healthz`, and `/readyz` return
   the configured denial while Prometheus can scrape `http://bourbonbook:8000/metrics` internally.
6. Run the account flow end to end: register, open the captured or delivered verification link,
   confirm verification, land on profile, set a screen name, change profile fields and password,
   request and complete a reset, and delete a test account.
7. Add a bottle using the selected Ollama analysis settings plus OpenAI grounded pricing, then verify
   final prices, clickable price sources, and admin API usage totals.
8. Exercise admin user actions from `/admin/users` and review `/admin/usage`.
9. Query Loki for JSON events such as `login_succeeded`, `admin_action`, and
   `ai_request_completed`; confirm secrets, one-time tokens, passwords, and user email addresses are
   absent from logs and metrics.
10. Remove `DEFAULT_ADMIN_PASSWORD` after first admin creation, restart, and confirm startup and
    login still work.

Local pre-PR validation remains:

```bash
make pr-review
```

For rollback, stop the new container, restore the backup from the host path currently configured by
`DATA_PATH`, and redeploy the previous image. Do not rely on schema downgrades as a substitute for a
database and uploads backup.

## iPhone installation

Serve the app over HTTPS, open it in Safari, choose **Share → Add to Home Screen**, and launch Bourbon Book from the new icon. The photo picker uses the rear camera when supported.

## Development

The Makefile is the canonical command interface for local development and CI:

```bash
make install       # install the exact uv.lock environment
make test          # fast deterministic tests
make coverage      # branch coverage with the enforced 90% floor
make pr-review     # all pre-PR gates plus the production image build
make help          # list every available target
```

During development, run focused tests as needed, then run `make pr-review` before opening or
updating a pull request. It checks lint and formatting, coverage, Bandit, the dependency lock and
known vulnerabilities, diff/tracked-file integrity, migrations, Compose configuration, and the
production Docker build. These checks use test configuration and do not load `.env`; only
`make run_local` loads that file. `build-local` builds the local Compose topology, while `build`
builds the production image used by CI and Unraid.

Repository administrators must configure the `main` branch ruleset to require the `quality`,
`security`, `dependency`, `review-readiness`, and `container` GitHub Actions jobs before merge.
Dependabot opens weekly Python, Actions, and Docker update pull requests, which must pass the same
required checks.

To intentionally upgrade the lock, run `make update`; it audits the upgraded environment and then
runs the complete non-container gate before returning success.

Run the app locally with proxy-header processing disabled:

```bash
make run_local
```

It binds to `127.0.0.1:8000` and defaults `SECURE_COOKIES` to false. Override `HOST` or `PORT` on the
Make command line when needed.

Evaluate either analysis provider against the bottle-image fixtures:

```bash
uv run --env-file .env python -m scripts.evaluate_ollama --provider ollama --model gemma3:4b
uv run --env-file .env python -m scripts.evaluate_ollama --provider openai --model gpt-5.5
```

The evaluator reports missing/unvalidated fixtures and scores the four primary vision fields:
product name, brand, fill level, and the status derived from that fill level. Product facts and
prices remain available for diagnostics but do not affect the vision score.

The workflows under `.github/workflows` follow the current `adhatcher-org` patterns: pull-request tests and container builds, plus a multi-architecture GHCR publish on `main`.
