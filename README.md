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

## Docker / Unraid

### Back up before the first migration-enabled release

Stop the existing Bourbon Book container before making a backup. Then copy or snapshot the complete
Unraid host directory configured by `DATA_PATH`, including both `bourbonbook.db` and `uploads/`.
For example, if `DATA_PATH` is `/mnt/user/appdata/bourbonbook`, back up that directory only after the
container has stopped. Do not make a normal file copy of `bourbonbook.db` while the application is
running; a live SQLite file copy may be inconsistent. Keep the backup until the upgraded container
has started successfully and the catalog and photos have been checked.

The container runs the migration bootstrap before Uvicorn. Startup intentionally fails with a schema
mismatch message if an unversioned database is partial or does not match the known legacy schema.

```bash
cp .env.example .env
docker network create bourbon-services  # once, if it does not already exist
docker network connect bourbon-services ollama  # once, for an existing Ollama container
docker compose up -d --build
```

Unraid settings:

- Repository: `ghcr.io/adhatcher-org/bourbonbook:latest`
- Web UI: port `8000` in the container; the Compose example publishes `8088`
- Persistent path: `/data` (map to `/mnt/user/appdata/bourbonbook`)
- Network: the same user-defined Docker network as the `ollama` container
- Required variable: `SESSION_SECRET` (generate with `openssl rand -hex 32`)
- Analysis provider: set `ANALYSIS_PROVIDER` to `ollama` or `openai`
- Ollama variables: Compose sets `OLLAMA_URL=http://ollama:11434`; the default vision model is `gemma3:4b`
- OpenAI variables: set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` (default `gpt-5.5`)
- Optional: set `SECURE_COOKIES=true` when the app is served behind HTTPS
- Public identity settings: `APP_ENV=production`,
  `PUBLIC_BASE_URL=https://bourbonbook.aaronhatcher.com`, `EMAIL_DELIVERY_MODE=smtp`, and the
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`,
  `SMTP_FROM_NAME`, and `SMTP_TLS_MODE` values supplied by your relay. Mark `SMTP_PASSWORD` as a
  masked secret in Unraid.
- Initial admin: set `DEFAULT_ADMIN_EMAIL` and masked `DEFAULT_ADMIN_PASSWORD` for the first start.
  The account is unverified and receives a verification link. Remove `DEFAULT_ADMIN_PASSWORD` after
  the first admin is created; later starts do not reapply bootstrap credentials.
- Proxy trust: set `PROXY_HEADERS=true` and restrict `FORWARDED_ALLOW_IPS` to SWAG's fixed container
  IP or the smallest proxy-network CIDR. Production rejects a missing allowlist and `*`. Local runs
  keep proxy processing disabled, so spoofed forwarding headers are ignored.
- Keep one Uvicorn worker. Identity rate limits are bounded and process-local; use a shared limiter
  before adding workers or replicas.

The SQLite database and normalized bottle photos live under `/data`. Logs go to stdout/stderr, and `/healthz` is used by the container health check.

For interactive recovery of the sole administrator, open a container terminal and run
`uv run python -m bourbonbook.admin_cli recover`. It prompts for secrets and does not accept a
password argument that could leak through shell history or the process list.

## iPhone installation

Serve the app over HTTPS, open it in Safari, choose **Share → Add to Home Screen**, and launch Bourbon Book from the new icon. The photo picker uses the rear camera when supported.

## Development

```bash
uv run ruff check .
uv run pytest --cov=bourbonbook
docker build -t bourbonbook .
```

Evaluate either analysis provider against the bottle-image fixtures:

```bash
uv run --env-file .env python -m scripts.evaluate_ollama --provider ollama --model gemma3:4b
uv run --env-file .env python -m scripts.evaluate_ollama --provider openai --model gpt-5.5
```

The evaluator reports missing/unvalidated fixtures and scores the four primary vision fields:
product name, brand, fill level, and the status derived from that fill level. Product facts and
prices remain available for diagnostics but do not affect the vision score.

The workflows under `.github/workflows` follow the current `adhatcher-org` patterns: pull-request tests and container builds, plus a multi-architecture GHCR publish on `main`.
