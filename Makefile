SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

UV ?= uv
PYTHON ?= python
IMAGE ?= bourbonbook
TAG ?= local-v1
HOST ?= 127.0.0.1
PORT ?= 8000
DATA_DIR ?= $(HOME)/Development/bourbonbook/data
BENCHMARK_ROOT ?= data/benchmarks
BENCHMARK_FIXTURE ?= $(BENCHMARK_ROOT)/fixtures/collection-v1
BENCHMARK_BASELINE ?= $(BENCHMARK_ROOT)/reports/current-baseline.json
BENCHMARK_CANDIDATE ?= $(BENCHMARK_ROOT)/reports/candidate.json
PRICE_CATALOG ?= $(DATA_DIR)/catalog-prices.jsonl
CATALOG_SCREENSHOTS ?= AmericanWhiskey1.png AmericanWhiskey2.png
CATALOG_SCREENSHOT_OUTPUT ?= $(DATA_DIR)/ohlq-screenshot-prices.jsonl

.PHONY: help install build build-local check pre-ci ci test coverage run_local update lint format \
	pr-review pr-check security dependency-check benchmark-export benchmark-run benchmark-compare \
	price-catalog-ingest price-catalog-reindex
	price-catalog-extract-screenshots

help: ## List development and CI targets.
	@awk 'BEGIN {FS = ":.*## "; printf "Bourbon Book targets:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install the exact locked application and development dependencies.
	$(UV) sync --frozen

build: ## Build the production container image.
	$(UV) run $(PYTHON) scripts/docker_build.py --image $(IMAGE) --tag $(TAG)

build-local: ## Build the local Compose service without pushing it.
	docker compose build bourbonbook

lint: ## Check Python lint and formatting without modifying files.
	$(UV) run --frozen --no-sync ruff check .
	$(UV) run --frozen --no-sync ruff format --check .

format: ## Format and automatically fix safe Python issues.
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test: ## Run the fast deterministic test suite.
	PYTHONDONTWRITEBYTECODE=1 $(UV) run --frozen --no-sync pytest -p no:cacheprovider

coverage: ## Run tests with branch coverage and enforce the temporary 80% floor.
	@coverage_file=$$(mktemp); \
	trap 'rm -f "$$coverage_file"' EXIT; \
	PYTHONDONTWRITEBYTECODE=1 COVERAGE_FILE="$$coverage_file" $(UV) run --frozen --no-sync pytest -p no:cacheprovider \
		--cov=bourbonbook --cov-branch --cov-report=term-missing --cov-fail-under=80

security: ## Scan maintained Python code with Bandit.
	$(UV) run --frozen --no-sync bandit --configfile pyproject.toml --severity-level medium \
		--confidence-level medium --recursive bourbonbook scripts

dependency-check: ## Validate the lock and audit the resolved environment.
	$(UV) lock --check
	for attempt in 1 2 3; do \
		$(UV) run --frozen --no-sync pip-audit && exit 0; \
		echo "pip-audit failed; retrying in $${attempt}0s" >&2; \
		sleep $$((attempt * 10)); \
	done; \
	$(UV) run --frozen --no-sync pip-audit

benchmark-export: ## Export the benchmark fixture into local data/benchmarks.
	@BENCHMARK_OWNER=$${BENCHMARK_OWNER:?set BENCHMARK_OWNER to the exact owner ID or username}; \
	$(UV) run --env-file data/.env python -m bourbonbook.benchmark_cli export \
		--owner "$$BENCHMARK_OWNER" \
		--output $(BENCHMARK_FIXTURE)

benchmark-run: ## Run the local benchmark fixture against the active provider.
	@echo "Running benchmark fixture $(BENCHMARK_FIXTURE)"
	$(UV) run --env-file data/.env python -m bourbonbook.benchmark_cli run \
		--fixture $(BENCHMARK_FIXTURE) \
		--output $(BENCHMARK_CANDIDATE) \
		--live \
		--preprocess-revision $${BENCHMARK_PREPROCESS_REVISION:-application-default} \
		--cold-start-state $${COLD_START_STATE:-uncontrolled}
	@echo "Benchmark report written to $(BENCHMARK_CANDIDATE)"

benchmark-compare: ## Compare the local benchmark baseline and candidate reports.
	@echo "Comparing $(BENCHMARK_BASELINE) against $(BENCHMARK_CANDIDATE)"
	$(UV) run --env-file $(DATA_DIR)/.env python -m bourbonbook.benchmark_cli compare \
		--baseline $(BENCHMARK_BASELINE) \
		--candidate $(BENCHMARK_CANDIDATE)

price-catalog-ingest: ## Import validated local price records from PRICE_CATALOG JSON Lines.
	$(UV) run --env-file $(DATA_DIR)/.env python -m bourbonbook.catalog_cli ingest-jsonl $(PRICE_CATALOG)

price-catalog-reindex: ## Rebuild the configured Qdrant price index from the SQLite catalog.
	$(UV) run --env-file $(DATA_DIR)/.env python -m bourbonbook.catalog_cli reindex

price-catalog-extract-screenshots: ## Extract local screenshot records through the configured Ollama vision model.
	$(UV) run --env-file $(DATA_DIR)/.env python -m scripts.extract_catalog_screenshots $(CATALOG_SCREENSHOTS) \
		--output $(CATALOG_SCREENSHOT_OUTPUT) --ingest

pr-check: ## Check diff hygiene, tracked secrets, migrations, and Compose configuration.
	$(UV) run --frozen --no-sync $(PYTHON) scripts/pr_review.py

check: lint test coverage security dependency-check pr-check ## Run all non-mutating quality gates.
	@echo "All non-mutating quality gates passed."

pre-ci: check ## Backward-compatible alias for the non-container quality gate.

ci: check build ## Run the complete local/GitHub CI gate.
	@echo "All CI gates, including the production image build, passed."

pr-review: check build ## Run every required check before opening or updating a PR.
	@echo "PR review checks passed; this branch is ready to push."

run_local: ## Run Uvicorn locally with reload and proxy trust disabled.
	SECURE_COOKIES=$${SECURE_COOKIES:-false} $(UV) run --env-file $(DATA_DIR)/.env uvicorn bourbonbook.main:app \
		--reload --host $(HOST) --port $(PORT) --no-proxy-headers

update: ## Upgrade dependencies intentionally, audit them, and run pre-CI.
	$(UV) lock --upgrade
	$(UV) sync
	$(MAKE) dependency-check
	$(MAKE) pre-ci
