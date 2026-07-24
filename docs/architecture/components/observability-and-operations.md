# Component Design: Observability & Operations

Modules: `bourbonbook/observability.py`, `bourbonbook/logging_config.py`, `bourbonbook/email.py`,
`bourbonbook/entrypoint.py`
Related: [HLDD](../hldd.md) · [Administration & configuration](admin-and-configuration.md)

## Responsibility

Give the operator enough signal to run Bourbon Book without an external telemetry stack being
required: Prometheus metrics, structured/redacted logs, a local AI-usage ledger, and a safe process
bootstrap — while deliberately never persisting sensitive content (prompts, responses, credentials,
bottle contents, email addresses) in any of these channels.

## AI usage ledger (`AIUsageRecorder` → `ApiUsage`)

Every AI/API provider call — Ollama, OpenAI analysis, OpenAI price search — is wrapped in
`usage_context(recorder, user_id)` and recorded via `AIUsageRecorder.record()`. The `ApiUsage` row
stores only: `provider`, `operation`, `model`, `success`, a truncated (`error_type[:40]`) bounded
error classification, `duration_ms`, token-count columns (input/output/total/cached/reasoning),
`web_search_calls`, an optional `user_id`, and a timestamp. There is structurally no column for
prompts, responses, bottle names, email addresses, URLs, or API keys — the README's privacy claim
about this ledger is enforced by the schema, not just by convention.
`bounded_error_type(exc)` classifies exceptions by class/type (`timeout`, `rate_limit`,
`provider_error`, `parse_error`, `network_error`, `unexpected`) and never captures exception message
text. `cleanup_old_records()` sweeps rows older than `API_USAGE_RETENTION_DAYS` (default 90) on
every startup.

## Prometheus metrics

| Metric | Type | Labels |
| --- | --- | --- |
| `bourbonbook_http_requests_total` | Counter | `method`, `route`, `status_class` |
| `bourbonbook_http_request_duration_seconds` | Histogram | `method`, `route` |
| `bourbonbook_http_requests_in_progress` | Gauge | `method`, `route` |
| `bourbonbook_auth_events_total` | Counter | `event`, `result` |
| `bourbonbook_ai_requests_total` | Counter | `provider`, `operation`, `model`, `result` |
| `bourbonbook_ai_request_duration_seconds` | Histogram | `provider`, `operation`, `model` |
| `bourbonbook_ai_tokens_total` | Counter | `provider`, `operation`, `model`, `direction` |
| `bourbonbook_openai_web_search_calls_total` | Counter | `operation`, `model` |
| `bourbonbook_email_deliveries_total` | Counter | `kind`, `result` |
| `bourbonbook_email_delivery_duration_seconds` | Histogram | `kind` |
| `bourbonbook_price_jobs_total`, `..._duration_seconds`, `..._current` | Counter/Histogram/Gauge | `result` / `state` |

`GET /metrics` returns 404 if `METRICS_ENABLED=false`, otherwise the standard Prometheus exposition
format. Request-level metrics are recorded by middleware in `main.py` (`route_template()` derives
the templated path from `request.scope["route"]`, falling back to `"unmatched"` for 404s so
unmatched-path cardinality doesn't explode the label set). Note the price-job gauges are defined but
should be verified against the current synchronous `refresh_prices()` call path before building a
dashboard around them (see HLDD §9 Open Gaps).

## Logging (`logging_config.py`)

- `configure_logging()` installs two handlers on the root logger: a stdout `StreamHandler`
  (text or JSON per `LOG_FORMAT`) and a `WatchedFileHandler` at `<DATA_DIR>/logs/bourbonbook.log`
  (always JSON regardless of console format). `WatchedFileHandler` detects inode changes from
  `logrotate` (rename/truncate) and reopens automatically — rotation-safe without the app needing to
  know rotation happened.
- **Redaction**: `RedactionFilter` (attached to both handlers) recursively scrubs any log record
  field/arg whose key matches `SENSITIVE_KEYS` (`authorization`, `api_key`, `cookie`, `csrf`,
  `form`, `password`, `secret`, `smtp_password`, `token`, etc.), replacing values with
  `"[REDACTED]"`. This applies to structured `extra` fields; a raw interpolated log message string
  (e.g., `CaptureEmailSender`'s dev-mode log line) is not itself redacted field-by-field, so new log
  call sites should prefer passing sensitive data via `extra=` rather than string interpolation.
- `log_event(logger, level, event, message, **fields)` is the app's structured-logging helper used
  throughout — every meaningful event (`ai_request_completed`, `email_delivery_succeeded/failed`,
  `admin_action`, `app_starting`/`app_stopping`, `login_failed`, `usage_retention_cleanup`, etc.) has
  a stable `event` name suitable for Loki/LogQL filtering.
- `uvicorn.access` logging is explicitly disabled in favor of the app's own request-observability
  middleware, which already emits an equivalent structured event per request.

## Email (`email.py`)

- `create_email_sender(settings)` returns `SMTPEmailSender` **only** when
  `email_delivery_mode == "smtp"` **and** `app_env == "production"`; every other combination
  (including SMTP mode configured but not in production) returns `CaptureEmailSender`, which holds
  messages in memory and surfaces the verification/reset link directly on the check-email page for
  local development.
- `SMTPEmailSender` runs synchronously in a thread (`asyncio.to_thread`), builds a plain-text +
  HTML-alternative message, and supports `starttls`/`ssl`/`none` TLS modes explicitly.
- `link_message()` renders the verification/reset templates from
  `bourbonbook/templates/email/{verification,password_reset}.{txt,html}` with HTML-escaped
  interpolation. `security_message()` is a hardcoded (non-templated) password-changed notice.
- `ObservedEmailSender` (in `observability.py`) wraps whichever sender is active: classifies each
  message by subject substring into `verification`/`reset`/`security`, times the send, increments
  `EMAIL_DELIVERIES`/`EMAIL_DURATION` metrics, and logs `email_delivery_succeeded`/`_failed`.

## Process bootstrap (`entrypoint.py`)

Sequence: `Settings.from_env()` → `configure_logging()` → `settings.validate_identity()` →
`bootstrap_database()` (Alembic) → `os.execvp("uvicorn", [...])`. Using `execvp` (not `subprocess`)
replaces the current process image in place — same PID — which is what makes the admin-restart
flow's `os.kill(os.getpid(), SIGTERM)` land on the actual serving uvicorn process rather than an
intermediate launcher. `main.create_app()`'s own `lifespan` re-runs several of these steps
idempotently (settings load, logging config, migration bootstrap, stale `ApiUsage` cleanup, admin
bootstrap) since it can also be invoked directly by a test harness or `uvicorn` reload, not only via
`entrypoint.py`.

`/healthz` (liveness only, always `200`) is what the container `HEALTHCHECK` and Unraid poll.
`/readyz` (DB connectivity + Alembic-at-head check, `503` if not ready) is the deeper check intended
for orchestration/monitoring, deliberately not used for the container's own health check to avoid a
crash-restart loop if migrations are merely still catching up.

## Design properties worth preserving

- The `ApiUsage` schema's lack of sensitive columns is a structural privacy guarantee — any change
  that adds a free-text or URL column to that table should be treated as a privacy-relevant design
  decision, not a routine schema tweak.
- Redaction is filter-based and keyed on field-name substrings; a new sensitive setting name should
  be checked against `SENSITIVE_KEYS` (or that list extended) before it's ever passed through
  `log_event(..., extra=...)`.
- `/healthz` vs `/readyz` intentionally serve different operational purposes; keep that split when
  adding new orchestration-facing checks rather than conflating them into one endpoint.
