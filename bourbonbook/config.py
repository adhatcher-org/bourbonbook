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

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            database_url=os.getenv(
                "DATABASE_URL", f"sqlite:///{data_dir / 'bourbonbook.db'}"
            ),
            session_secret=os.getenv("SESSION_SECRET", secrets.token_urlsafe(32)),
            secure_cookies=os.getenv("SECURE_COOKIES", "false").lower() == "true",
            ollama_url=os.getenv(
                "OLLAMA_URL", "https://ollama.aaronhatcher.com"
            ).rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:4b"),
            max_users=int(os.getenv("MAX_USERS", "10")),
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "15")),
            analysis_provider=os.getenv("ANALYSIS_PROVIDER", "ollama").strip().lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        )
