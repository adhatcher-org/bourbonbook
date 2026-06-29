from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    session_secret: str
    secure_cookies: bool
    ollama_url: str
    ollama_model: str
    max_users: int
    max_upload_mb: int
    analysis_provider: str = "ollama"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.5"
    public_base_url: str = "http://localhost:8000"
    email_delivery_mode: str = "capture"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = "bourbonbook@example.invalid"
    smtp_from_name: str = "Bourbon Book"
    smtp_tls_mode: str = "starttls"
    verification_ttl_hours: int = 24
    reset_ttl_minutes: int = 60
    default_admin_email: str | None = None
    default_admin_password: str | None = None
    app_env: str = "development"
    rate_limit_secret: str | None = None
    proxy_headers: bool = False
    forwarded_allow_ips: str = "127.0.0.1"
    rate_limit_attempts: int = 8
    rate_limit_window_seconds: int = 300
    rate_limit_global_attempts: int = 200
    metrics_enabled: bool = True
    api_usage_retention_days: int = 90
    log_level: str = "INFO"
    log_format: str = "text"

    @classmethod
    def from_env(cls) -> Settings:
        from bourbonbook.admin_config import load_managed_overrides

        values: Mapping[str, str] = {**os.environ, **load_managed_overrides()}
        get = values.get
        data_dir = Path(get("DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            database_url=get("DATABASE_URL", f"sqlite:///{data_dir / 'bourbonbook.db'}"),
            session_secret=get("SESSION_SECRET", secrets.token_urlsafe(32)),
            secure_cookies=get("SECURE_COOKIES", "false").lower() == "true",
            ollama_url=get("OLLAMA_URL", "https://ollama.aaronhatcher.com").rstrip("/"),
            ollama_model=get("OLLAMA_MODEL", "gemma3:4b"),
            max_users=int(get("MAX_USERS", "10")),
            max_upload_mb=int(get("MAX_UPLOAD_MB", "15")),
            analysis_provider=get("ANALYSIS_PROVIDER", "ollama").strip().lower(),
            openai_api_key=get("OPENAI_API_KEY") or None,
            openai_model=get("OPENAI_MODEL", "gpt-5.5"),
            public_base_url=get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
            email_delivery_mode=get("EMAIL_DELIVERY_MODE", "capture").lower(),
            smtp_host=get("SMTP_HOST") or None,
            smtp_port=int(get("SMTP_PORT", "587")),
            smtp_username=get("SMTP_USERNAME") or None,
            smtp_password=get("SMTP_PASSWORD") or None,
            smtp_from_email=get("SMTP_FROM_EMAIL", "bourbonbook@example.invalid"),
            smtp_from_name=get("SMTP_FROM_NAME", "Bourbon Book"),
            smtp_tls_mode=get("SMTP_TLS_MODE", "starttls").lower(),
            verification_ttl_hours=int(get("VERIFICATION_TTL_HOURS", "24")),
            reset_ttl_minutes=int(get("RESET_TTL_MINUTES", "60")),
            default_admin_email=get("DEFAULT_ADMIN_EMAIL") or None,
            default_admin_password=get("DEFAULT_ADMIN_PASSWORD") or None,
            app_env=get("APP_ENV", "development").lower(),
            rate_limit_secret=get("RATE_LIMIT_SECRET") or None,
            proxy_headers=get("PROXY_HEADERS", "false").lower() == "true",
            forwarded_allow_ips=get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
            rate_limit_attempts=int(get("RATE_LIMIT_ATTEMPTS", "8")),
            rate_limit_window_seconds=int(get("RATE_LIMIT_WINDOW_SECONDS", "300")),
            rate_limit_global_attempts=int(get("RATE_LIMIT_GLOBAL_ATTEMPTS", "200")),
            metrics_enabled=get("METRICS_ENABLED", "true").lower() == "true",
            api_usage_retention_days=int(get("API_USAGE_RETENTION_DAYS", "90")),
            log_level=get("LOG_LEVEL", "INFO").upper(),
            log_format=get(
                "LOG_FORMAT",
                "json" if get("APP_ENV", "development").lower() == "production" else "text",
            ).lower(),
        )

    def validate_identity(self) -> None:
        if self.email_delivery_mode not in {"smtp", "capture"}:
            raise ValueError("EMAIL_DELIVERY_MODE must be smtp or capture")
        if self.smtp_tls_mode not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP_TLS_MODE must be starttls, ssl, or none")
        if self.email_delivery_mode == "smtp" and not self.smtp_host:
            raise ValueError("SMTP_HOST is required when EMAIL_DELIVERY_MODE=smtp")
        if self.app_env == "production" and not self.public_base_url.startswith("https://"):
            raise ValueError("PUBLIC_BASE_URL must use HTTPS in production")
        if self.app_env == "production":
            forwarded_allow_ips = [
                value.strip() for value in self.forwarded_allow_ips.split(",") if value.strip()
            ]
            if not self.secure_cookies:
                raise ValueError("Production requires SECURE_COOKIES=true")
            if not self.proxy_headers or not forwarded_allow_ips or "*" in forwarded_allow_ips:
                raise ValueError(
                    "Production requires PROXY_HEADERS=true and a restricted FORWARDED_ALLOW_IPS"
                )
        if self.log_format not in {"json", "text"}:
            raise ValueError("LOG_FORMAT must be json or text")
