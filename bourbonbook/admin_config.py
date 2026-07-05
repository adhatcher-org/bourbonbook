from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
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
    ConfigField("OLLAMA_MODEL", "ollama_model", "Ollama model", "Analysis"),
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

SECRET_PLACEHOLDER = ""


def managed_config_path(settings: Settings) -> Path:
    return settings.data_dir / ".env"


def read_managed_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in {field.key for field in CONFIG_FIELDS}:
            continue
        raw_value = raw_value.strip()
        try:
            value = json.loads(raw_value) if raw_value.startswith('"') else raw_value
        except json.JSONDecodeError:
            value = raw_value
        values[key] = str(value)
    return values


def write_managed_config(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    lines = ["# Managed from Bourbon Book administration. Changes require a restart."]
    lines.extend(f"{field.key}={json.dumps(values[field.key])}" for field in CONFIG_FIELDS)
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
