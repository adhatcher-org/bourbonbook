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

## Docker / Unraid

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

The SQLite database and normalized bottle photos live under `/data`. Logs go to stdout/stderr, and `/healthz` is used by the container health check.

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
