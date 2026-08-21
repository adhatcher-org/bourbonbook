from __future__ import annotations

import json
import os
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from bourbonbook.config import Settings


@dataclass(frozen=True)
class ConfigField:
    key: str
    attribute: str
    label: str
    group: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    secret: bool = False
    optional: bool = False


CONFIG_FIELDS = (
    ConfigField("SESSION_SECRET", "session_secret", "Session secret", "Application", secret=True),
    ConfigField("SECURE_COOKIES", "secure_cookies", "Secure cookies", "Application", "boolean"),
    ConfigField(
        "ANALYSIS_PROVIDER",
        "analysis_provider",
        "Analysis provider",
        "Analysis",
        "choice",
        ("ollama", "openai"),
    ),
    ConfigField("OLLAMA_URL", "ollama_url", "Ollama URL", "Analysis", "url"),
    ConfigField("OLLAMA_MODEL", "ollama_model", "Ollama fallback model", "Analysis"),
    ConfigField(
        "OLLAMA_NUM_CTX",
        "ollama_num_ctx",
        "Ollama fallback/text context window",
        "Analysis",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "OLLAMA_VISION_MODEL",
        "ollama_vision_model",
        "Ollama vision model",
        "Analysis",
        optional=True,
    ),
    ConfigField(
        "OLLAMA_TEXT_MODEL",
        "ollama_text_model",
        "Ollama text model",
        "Analysis",
        optional=True,
    ),
    ConfigField(
        "OLLAMA_VISION_NUM_CTX",
        "ollama_vision_num_ctx",
        "Ollama vision context window",
        "Analysis",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "OLLAMA_TEXT_NUM_CTX",
        "ollama_text_num_ctx",
        "Ollama text context window",
        "Analysis",
        "integer",
        minimum=1,
        optional=True,
    ),
    ConfigField(
        "OLLAMA_API_KEY",
        "ollama_api_key",
        "Ollama Cloud API key",
        "Analysis",
        secret=True,
        optional=True,
    ),
    ConfigField("QDRANT_URL", "qdrant_url", "Qdrant URL", "Pricing", "url", optional=True),
    ConfigField(
        "QDRANT_API_KEY", "qdrant_api_key", "Qdrant API key", "Pricing", secret=True, optional=True
    ),
    ConfigField(
        "QDRANT_PRICE_COLLECTION",
        "qdrant_price_collection",
        "Qdrant price collection",
        "Pricing",
    ),
    ConfigField(
        "OPENAI_API_KEY", "openai_api_key", "OpenAI API key", "Analysis", secret=True, optional=True
    ),
    ConfigField("OPENAI_MODEL", "openai_model", "OpenAI model", "Analysis"),
    ConfigField("MAX_USERS", "max_users", "Maximum users", "Application", "integer", minimum=1),
    ConfigField(
        "MAX_UPLOAD_MB",
        "max_upload_mb",
        "Maximum upload (MB)",
        "Application",
        "integer",
        minimum=1,
        maximum=100,
    ),
    ConfigField(
        "CATALOG_IMPORT_MAX_FILES",
        "catalog_import_max_files",
        "Catalog import maximum files",
        "Catalog import",
        "integer",
        minimum=1,
        maximum=20,
    ),
    ConfigField(
        "CATALOG_IMPORT_MAX_TOTAL_MB",
        "catalog_import_max_total_mb",
        "Catalog import maximum total (MB)",
        "Catalog import",
        "integer",
        minimum=1,
        maximum=500,
    ),
    ConfigField(
        "CATALOG_IMPORT_MAX_PDF_PAGES",
        "catalog_import_max_pdf_pages",
        "Catalog import maximum PDF pages",
        "Catalog import",
        "integer",
        minimum=1,
        maximum=100,
    ),
    ConfigField(
        "CATALOG_IMPORT_MAX_IMAGE_PIXELS",
        "catalog_import_max_image_pixels",
        "Catalog import maximum image pixels",
        "Catalog import",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "CATALOG_IMPORT_MAX_IMAGE_DIMENSION",
        "catalog_import_max_image_dimension",
        "Catalog import maximum image dimension (0 disables)",
        "Catalog import",
        "integer",
        minimum=0,
    ),
    ConfigField(
        "CATALOG_IMPORT_SOURCE_EXPIRY_HOURS",
        "catalog_import_source_expiry_hours",
        "Catalog import source expiry (hours)",
        "Catalog import",
        "integer",
        minimum=1,
        maximum=720,
    ),
    ConfigField(
        "APP_ENV", "app_env", "Environment", "Application", "choice", ("development", "production")
    ),
    ConfigField("PUBLIC_BASE_URL", "public_base_url", "Public base URL", "Application", "url"),
    ConfigField(
        "EMAIL_DELIVERY_MODE",
        "email_delivery_mode",
        "Email delivery",
        "Email",
        "choice",
        ("capture", "smtp"),
    ),
    ConfigField("SMTP_HOST", "smtp_host", "SMTP host", "Email", optional=True),
    ConfigField(
        "SMTP_PORT", "smtp_port", "SMTP port", "Email", "integer", minimum=1, maximum=65535
    ),
    ConfigField("SMTP_USERNAME", "smtp_username", "SMTP username", "Email", optional=True),
    ConfigField(
        "SMTP_PASSWORD", "smtp_password", "SMTP password", "Email", secret=True, optional=True
    ),
    ConfigField("SMTP_FROM_EMAIL", "smtp_from_email", "From email", "Email", "email"),
    ConfigField("SMTP_FROM_NAME", "smtp_from_name", "From name", "Email"),
    ConfigField(
        "SMTP_TLS_MODE",
        "smtp_tls_mode",
        "SMTP security",
        "Email",
        "choice",
        ("starttls", "ssl", "none"),
    ),
    ConfigField(
        "VERIFICATION_TTL_HOURS",
        "verification_ttl_hours",
        "Verification lifetime (hours)",
        "Email",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "EMAIL_VERIFICATION_REQUIRED",
        "email_verification_required",
        "Require email verification",
        "Email",
        "boolean",
    ),
    ConfigField(
        "RESET_TTL_MINUTES",
        "reset_ttl_minutes",
        "Reset lifetime (minutes)",
        "Email",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "DEFAULT_ADMIN_EMAIL",
        "default_admin_email",
        "Bootstrap admin email",
        "Bootstrap",
        "email",
        optional=True,
    ),
    ConfigField(
        "DEFAULT_ADMIN_PASSWORD",
        "default_admin_password",
        "Bootstrap admin password",
        "Bootstrap",
        secret=True,
        optional=True,
    ),
    ConfigField("PROXY_HEADERS", "proxy_headers", "Trust proxy headers", "Network", "boolean"),
    ConfigField("FORWARDED_ALLOW_IPS", "forwarded_allow_ips", "Trusted proxy IPs", "Network"),
    ConfigField(
        "RATE_LIMIT_ATTEMPTS",
        "rate_limit_attempts",
        "Rate limit attempts",
        "Security",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "RATE_LIMIT_WINDOW_SECONDS",
        "rate_limit_window_seconds",
        "Rate limit window (seconds)",
        "Security",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "RATE_LIMIT_GLOBAL_ATTEMPTS",
        "rate_limit_global_attempts",
        "Global rate limit attempts",
        "Security",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "METRICS_ENABLED", "metrics_enabled", "Prometheus metrics", "Observability", "boolean"
    ),
    ConfigField(
        "API_USAGE_RETENTION_DAYS",
        "api_usage_retention_days",
        "Usage retention (days)",
        "Observability",
        "integer",
        minimum=1,
    ),
    ConfigField(
        "LOG_LEVEL",
        "log_level",
        "Log level",
        "Observability",
        "choice",
        ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    ),
    ConfigField(
        "LOG_FORMAT", "log_format", "Log format", "Observability", "choice", ("text", "json")
    ),
)


ENV_ONLY_SETTINGS: Mapping[str, str] = MappingProxyType(
    {
        # Deliberate policy. These must never become admin-editable.
        "data_dir": (
            "Locates the managed .env itself; an admin-set value could redirect the very file "
            "the registry is read from and written to."
        ),
        "database_url": (
            "Derived from data_dir. Repointing it from the UI would strand the live database "
            "and the Alembic revision the running process already applied."
        ),
        "rate_limit_secret": (
            "Seeds the rate-limit HMAC. Rotating it from the UI would silently reset every live "
            "bucket, handing an attacker a way to clear their own lockout."
        ),
        # Acknowledged drift, not policy: catalog-import tuning budgets that are admin-editable in
        # principle and simply have no ConfigField yet. Give one a ConfigField and delete its entry
        # here; do not add new entries to this block without a reason that belongs above instead.
        "catalog_import_max_pdf_render_pixels": (
            "Decode budget; env-only pending an admin UI decision."
        ),
        "catalog_import_max_pdf_render_dimension": (
            "Decode budget; env-only pending an admin UI decision."
        ),
        "catalog_import_queue_capacity": "Queue budget; env-only pending an admin UI decision.",
        "catalog_import_chunk_timeout_seconds": (
            "Worker timing; env-only pending an admin UI decision."
        ),
        "catalog_import_batch_timeout_seconds": (
            "Worker timing; env-only pending an admin UI decision."
        ),
        "catalog_import_lease_seconds": "Worker lease; env-only pending an admin UI decision.",
        "catalog_import_lease_heartbeat_seconds": (
            "Worker lease; env-only pending an admin UI decision."
        ),
        "catalog_import_poll_seconds": "Worker timing; env-only pending an admin UI decision.",
    }
)
"""Settings attributes deliberately absent from :data:`CONFIG_FIELDS`, each with a reason.

A missing ``ConfigField`` is otherwise silent -- the setting keeps working but becomes invisible to
the admin UI, so drift is only ever found by reading the code. ``tests/test_config_registry.py``
asserts every ``Settings`` attribute is either registered or listed here, which turns the next
omission into a failing test rather than an accident.
"""

SECRET_PLACEHOLDER = ""

UNMANAGED_HEADER = "# Unregistered keys (preserved, not managed via UI)"

MANAGED_HEADER = "# Managed from Bourbon Book administration. Changes require a restart."

# Keys that may sit in the managed .env without being a mistake. Everything in
# ENV_ONLY_SETTINGS is deliberately absent here: those are settable from the
# container environment but silently discarded when read from this file, so an
# operator who puts one here does need telling.
NON_SETTING_ENV_KEYS = frozenset(
    {
        # Consumed by the container runtime, not by Settings.
        "TZ",
    }
)


def managed_config_path(settings: Settings) -> Path:
    return settings.data_dir / ".env"


def _iter_config_assignments(path: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(key, raw_value)`` for every assignment line, in file order.

    Comments and blank lines are skipped. Later duplicates are yielded too, so
    callers decide which occurrence wins.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        yield key.strip(), raw_value.strip()


def _decode_value(raw_value: str) -> str:
    try:
        value = json.loads(raw_value) if raw_value.startswith('"') else raw_value
    except json.JSONDecodeError:
        value = raw_value
    return str(value)


def read_managed_config(path: Path) -> dict[str, str]:
    """Return the values this module manages, keyed by ConfigField key.

    Deliberately limited to registered keys: the result is layered over
    ``os.environ`` by :func:`load_managed_overrides`, and unregistered keys such
    as ``DATA_DIR`` must not be able to redirect the very path this file was read
    from. Use :func:`unmanaged_config_entries` to see the rest.
    """
    managed = {field.key for field in CONFIG_FIELDS}
    return {
        key: _decode_value(raw_value)
        for key, raw_value in _iter_config_assignments(path)
        if key in managed
    }


def unmanaged_config_entries(path: Path) -> dict[str, str]:
    """Return assignments no ConfigField owns, with values kept verbatim.

    These are operator-maintained keys (``DATA_DIR``, ``DEBUG``, tuning
    knobs with no admin UI). Values are not decoded or re-encoded so that a
    round-trip through :func:`write_managed_config` is byte-stable.
    """
    managed = {field.key for field in CONFIG_FIELDS}
    entries: dict[str, str] = {}
    for key, raw_value in _iter_config_assignments(path):
        if key not in managed:
            entries[key] = raw_value
    return entries


def unregistered_env_entries(path: Path) -> dict[str, str]:
    """Return ``.env`` assignments that are silently ignored at runtime.

    :func:`read_managed_config` drops every key no ``ConfigField`` owns, so an
    operator can set one, see it in the file, and never have it take effect.
    Subtracting the keys that are unmanaged *by design* leaves exactly the
    entries worth warning about.
    """
    return {
        key: raw_value
        for key, raw_value in unmanaged_config_entries(path).items()
        if key not in NON_SETTING_ENV_KEYS
    }


def config_sources(path: Path, environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return where each registered key's value comes from.

    ``"file"`` means the managed ``.env`` set it (and therefore wins),
    ``"environment"`` means it is inherited from the container environment, and
    ``"default"`` means neither source set it and the code default applies.
    """
    environment = os.environ if environment is None else environment
    from_file = read_managed_config(path)
    sources: dict[str, str] = {}
    for field in CONFIG_FIELDS:
        if field.key in from_file:
            sources[field.key] = "file"
        elif field.key in environment:
            sources[field.key] = "environment"
        else:
            sources[field.key] = "default"
    return sources


def persisted_keys(
    submitted: Mapping[str, str],
    baseline: Mapping[str, str],
    existing: Mapping[str, str],
    reverted: Collection[str] = (),
) -> set[str]:
    """Return the registered keys a save should write to ``.env``.

    A key belongs in the file when it is already there (an existing deliberate
    override) or when the submitted value differs from ``baseline`` -- what the
    container environment and code defaults would produce on their own. Keys
    that match the baseline are omitted so they keep inheriting rather than
    being frozen into the file, which would make later environment changes
    silently ineffective. ``reverted`` names keys the admin explicitly asked to
    hand back to the environment.
    """
    keys: set[str] = set()
    for field in CONFIG_FIELDS:
        key = field.key
        if key in reverted:
            continue
        if key in existing or submitted.get(key, "") != baseline.get(key, ""):
            keys.add(key)
    return keys


def write_managed_config(
    path: Path, values: Mapping[str, str], *, persist: Collection[str] | None = None
) -> None:
    """Atomically rewrite the managed ``.env``.

    ``persist`` names the registered keys to write. Anything omitted is dropped
    from the file so it falls back to the container environment or the code
    default; ``None`` writes every registered key. Lines the registry does not
    own -- comments, blanks, and unregistered assignments -- are carried through
    verbatim in their original order, so operator notes survive a save.
    """
    managed = {field.key for field in CONFIG_FIELDS}
    persisted = managed if persist is None else set(persist)
    path.parent.mkdir(parents=True, exist_ok=True)

    kept: list[str] = []
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line in {MANAGED_HEADER, UNMANAGED_HEADER}:
                continue
            is_assignment = bool(line) and not line.startswith("#") and "=" in line
            if is_assignment and line.split("=", 1)[0].strip() in managed:
                continue
            kept.append(raw_line)
    while kept and not kept[-1].strip():
        kept.pop()

    lines = list(kept)
    if lines:
        lines.append("")
    lines.append(MANAGED_HEADER)
    lines.extend(
        f"{field.key}={json.dumps(values[field.key])}"
        for field in CONFIG_FIELDS
        if field.key in persisted
    )
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def settings_values(settings: Settings) -> dict[str, str]:
    values = {}
    for field in CONFIG_FIELDS:
        value = getattr(settings, field.attribute)
        if isinstance(value, bool):
            values[field.key] = "true" if value else "false"
        else:
            values[field.key] = "" if value is None else str(value)
    return values


def parse_config_form(
    form: Mapping[str, Any], current: Settings
) -> tuple[dict[str, str], Settings]:
    current_values = settings_values(current)
    values: dict[str, str] = {}
    attributes: dict[str, Any] = {}
    errors: list[str] = []
    for field in CONFIG_FIELDS:
        raw = str(form.get(field.key, "")).strip()
        if field.secret and not raw and str(form.get(f"clear_{field.key}", "")) != "true":
            raw = current_values[field.key]
        try:
            parsed, serialized = _parse_field(field, raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        attributes[field.attribute] = parsed
        values[field.key] = serialized
    if errors:
        raise ValueError(" ".join(errors))
    candidate = Settings(**{**vars(current), **attributes})
    candidate.validate_identity()
    if candidate.analysis_provider == "openai" and not candidate.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required when ANALYSIS_PROVIDER=openai")
    return values, candidate


def _parse_field(field: ConfigField, raw: str) -> tuple[Any, str]:
    if not raw and field.optional:
        return None, ""
    if not raw:
        raise ValueError(f"{field.key} is required.")
    if field.kind == "boolean":
        if raw not in {"true", "false"}:
            raise ValueError(f"{field.key} must be true or false.")
        return raw == "true", raw
    if field.kind == "choice":
        normalized = raw.upper() if field.key == "LOG_LEVEL" else raw.lower()
        if normalized not in field.options:
            raise ValueError(f"{field.key} must be one of: {', '.join(field.options)}.")
        return normalized, normalized
    if field.kind == "integer":
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{field.key} must be a whole number.") from exc
        if field.minimum is not None and value < field.minimum:
            raise ValueError(f"{field.key} must be at least {field.minimum}.")
        if field.maximum is not None and value > field.maximum:
            raise ValueError(f"{field.key} must be at most {field.maximum}.")
        return value, str(value)
    if field.kind == "url":
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"{field.key} must be a valid HTTP or HTTPS URL.")
        raw = raw.rstrip("/")
    if field.kind == "email" and ("@" not in raw or raw.startswith("@") or raw.endswith("@")):
        raise ValueError(f"{field.key} must be a valid email address.")
    if field.key == "SESSION_SECRET" and len(raw) < 32:
        raise ValueError("SESSION_SECRET must be at least 32 characters.")
    if field.key == "DEFAULT_ADMIN_PASSWORD" and raw and len(raw) < 10:
        raise ValueError("DEFAULT_ADMIN_PASSWORD must be at least 10 characters.")
    return raw, raw


def load_managed_overrides(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = environment or os.environ
    data_dir = Path(environment.get("DATA_DIR", "./data")).expanduser().resolve()
    return read_managed_config(data_dir / ".env")
