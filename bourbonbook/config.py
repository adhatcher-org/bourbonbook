from __future__ import annotations

import os
import secrets
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
        data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{data_dir / 'bourbonbook.db'}"),
            session_secret=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)),
            secure_cookies=os.getenv("SECURE_COOKIES", "false").lower() == "true",
            ollama_url=os.getenv("OLLAMA_URL", "https://ollama.aaronhatcher.com").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:4b"),
            max_users=int(os.getenv("MAX_USERS", "10")),
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "15")),
            analysis_provider=os.getenv("ANALYSIS_PROVIDER", "ollama").strip().lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"),
            email_delivery_mode=os.getenv("EMAIL_DELIVERY_MODE", "capture").lower(),
            smtp_host=os.getenv("SMTP_HOST") or None,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            smtp_from_email=os.getenv("SMTP_FROM_EMAIL", "bourbonbook@example.invalid"),
            smtp_from_name=os.getenv("SMTP_FROM_NAME", "Bourbon Book"),
            smtp_tls_mode=os.getenv("SMTP_TLS_MODE", "starttls").lower(),
            verification_ttl_hours=int(os.getenv("VERIFICATION_TTL_HOURS", "24")),
            reset_ttl_minutes=int(os.getenv("RESET_TTL_MINUTES", "60")),
            default_admin_email=os.getenv("DEFAULT_ADMIN_EMAIL") or None,
            default_admin_password=os.getenv("DEFAULT_ADMIN_PASSWORD") or None,
            app_env=os.getenv("APP_ENV", "development").lower(),
            rate_limit_secret=os.getenv("RATE_LIMIT_SECRET") or None,
            proxy_headers=os.getenv("PROXY_HEADERS", "false").lower() == "true",
            forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
            rate_limit_attempts=int(os.getenv("RATE_LIMIT_ATTEMPTS", "8")),
            rate_limit_window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300")),
            rate_limit_global_attempts=int(os.getenv("RATE_LIMIT_GLOBAL_ATTEMPTS", "200")),
            metrics_enabled=os.getenv("METRICS_ENABLED", "true").lower() == "true",
            api_usage_retention_days=int(os.getenv("API_USAGE_RETENTION_DAYS", "90")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_format=os.getenv(
                "LOG_FORMAT",
                "json" if os.getenv("APP_ENV", "development").lower() == "production" else "text",
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
        if self.app_env == "production" and (
            not self.proxy_headers
            or not self.forwarded_allow_ips.strip()
            or self.forwarded_allow_ips.strip() == "*"
        ):
            raise ValueError(
                "Production requires PROXY_HEADERS=true and a restricted FORWARDED_ALLOW_IPS"
            )
        if self.log_format not in {"json", "text"}:
            raise ValueError("LOG_FORMAT must be json or text")
