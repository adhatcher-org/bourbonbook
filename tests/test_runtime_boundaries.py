from __future__ import annotations

import asyncio
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from bourbonbook import admin_cli, entrypoint
from bourbonbook.auth import hash_password, verify_password
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.email import OutgoingEmail, SMTPEmailSender, create_email_sender, link_message
from bourbonbook.models import User
from bourbonbook.photos import save_photo


def settings_for(tmp_path: Path, **changes) -> Settings:
    values = {
        "data_dir": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'runtime.db'}",
        "session_secret": "test-secret",
        "secure_cookies": False,
        "ollama_url": "http://ollama.invalid",
        "ollama_model": "test",
        "max_users": 10,
        "max_upload_mb": 2,
    }
    values.update(changes)
    return Settings(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"email_delivery_mode": "pigeon"}, "EMAIL_DELIVERY_MODE"),
        ({"smtp_tls_mode": "sometimes"}, "SMTP_TLS_MODE"),
        ({"email_delivery_mode": "smtp"}, "SMTP_HOST"),
        ({"app_env": "production"}, "HTTPS"),
        (
            {
                "app_env": "production",
                "public_base_url": "https://example.com",
                "secure_cookies": True,
                "proxy_headers": False,
            },
            "PROXY_HEADERS",
        ),
        (
            {
                "app_env": "production",
                "public_base_url": "https://example.com",
                "secure_cookies": False,
                "proxy_headers": True,
                "forwarded_allow_ips": "172.18.0.4",
            },
            "SECURE_COOKIES",
        ),
        (
            {
                "app_env": "production",
                "public_base_url": "https://example.com",
                "secure_cookies": True,
                "proxy_headers": True,
                "forwarded_allow_ips": "172.18.0.4, *",
            },
            "restricted FORWARDED_ALLOW_IPS",
        ),
    ],
)
def test_identity_configuration_boundaries(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        settings_for(tmp_path, **changes).validate_identity()


def test_settings_from_environment_parses_and_normalizes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECURE_COOKIES", "TRUE")
    monkeypatch.setenv("ANALYSIS_PROVIDER", " OPENAI ")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.invalid/")
    monkeypatch.setenv("MAX_USERS", "7")
    monkeypatch.setenv("PROXY_HEADERS", "true")

    settings = Settings.from_env()

    assert settings.data_dir == tmp_path.resolve()
    assert settings.secure_cookies is True
    assert settings.analysis_provider == "openai"
    assert settings.ollama_url == "http://ollama.invalid"
    assert settings.max_users == 7
    assert settings.proxy_headers is True


class FakeSMTP:
    instance: FakeSMTP

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.connection = (host, port, timeout)
        self.started_tls = False
        self.credentials: tuple[str, str] | None = None
        self.message: EmailMessage | None = None
        type(self).instance = self

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def starttls(self, *, context) -> None:
        self.started_tls = context is not None

    def login(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.message = message


def test_smtp_sender_builds_message_and_uses_tls(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("bourbonbook.email.smtplib.SMTP", FakeSMTP)
    settings = settings_for(
        tmp_path,
        email_delivery_mode="smtp",
        smtp_host="smtp.example.com",
        smtp_username="sender",
        smtp_password="secret",
        smtp_from_email="book@example.com",
    )
    sender = create_email_sender(settings)
    outgoing = OutgoingEmail("reader@example.com", "Subject", "Plain", "<p>HTML</p>")

    assert isinstance(sender, SMTPEmailSender)
    asyncio.run(sender.send(outgoing))
    smtp = FakeSMTP.instance
    assert smtp.connection == ("smtp.example.com", 587, 15)
    assert smtp.started_tls
    assert smtp.credentials == ("sender", "secret")
    assert smtp.message["To"] == "reader@example.com"


def test_link_messages_escape_html_and_choose_reset_template() -> None:
    message = link_message(
        "reader@example.com", "reset_password", "https://example.com/?a=1&b=2", "10 < 20"
    )

    assert message.subject.startswith("Reset your password")
    assert "https://example.com/?a=1&b=2" in message.text
    assert "&amp;" in message.html
    assert "&lt;" in message.html


def test_entrypoint_builds_trusted_and_untrusted_proxy_commands(
    monkeypatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def fake_exec(_program: str, command: list[str]) -> None:
        commands.append(command)
        raise RuntimeError("exec stopped")

    monkeypatch.setattr(entrypoint, "bootstrap_database", lambda _settings: None)
    monkeypatch.setattr(entrypoint.os, "execvp", fake_exec)
    for proxy_headers in (False, True):
        settings = settings_for(
            tmp_path,
            proxy_headers=proxy_headers,
            forwarded_allow_ips="172.18.0.4",
        )
        monkeypatch.setattr(entrypoint.Settings, "from_env", lambda current=settings: current)
        with pytest.raises(RuntimeError, match="exec stopped"):
            entrypoint.main()

    assert "--no-proxy-headers" in commands[0]
    assert commands[1][-3:] == ["--proxy-headers", "--forwarded-allow-ips", "172.18.0.4"]


def test_admin_recovery_updates_credentials_and_revokes_sessions(
    monkeypatch, tmp_path: Path
) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings)
    database.create_all()
    with database.session_factory() as session:
        session.add(
            User(
                username="old@example.com",
                display_name="Owner",
                email="old@example.com",
                password_hash=hash_password("original-password"),
                is_admin=True,
            )
        )
        session.commit()
    database.engine.dispose()

    monkeypatch.setattr(admin_cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr("builtins.input", lambda _prompt: "NEW@example.com")
    monkeypatch.setattr(admin_cli.getpass, "getpass", lambda _prompt: "replacement-password")
    admin_cli.recover()

    verify_database = Database(settings)
    with verify_database.session_factory() as session:
        admin = session.scalar(select(User).where(User.is_admin.is_(True)))
        assert admin.email == "new@example.com"
        assert admin.session_version == 2
        assert verify_password("replacement-password", admin.password_hash)
    verify_database.engine.dispose()


def test_admin_recovery_requires_exactly_one_admin(monkeypatch, tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings)
    database.create_all()
    database.engine.dispose()
    monkeypatch.setattr(admin_cli.Settings, "from_env", lambda: settings)

    with pytest.raises(SystemExit, match="exactly one administrator"):
        admin_cli.recover()


def test_invalid_and_oversized_photos_are_rejected(tmp_path: Path) -> None:
    invalid = UploadFile(filename="bad.jpg", file=__import__("io").BytesIO(b"not-an-image"))
    with pytest.raises(HTTPException) as invalid_error:
        asyncio.run(save_photo(invalid, tmp_path, 1))
    assert invalid_error.value.status_code == 400

    oversized = UploadFile(filename="huge.jpg", file=__import__("io").BytesIO(b"x" * 10))
    with pytest.raises(HTTPException) as size_error:
        asyncio.run(save_photo(oversized, tmp_path, 0))
    assert size_error.value.status_code == 413
