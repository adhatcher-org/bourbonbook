from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from bourbonbook.auth import hash_password, verify_password
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.email import MemoryEmailSender
from bourbonbook.identity import bootstrap_admin
from bourbonbook.models import User


def settings_for(tmp_path: Path, **changes) -> Settings:
    values = {
        "data_dir": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'identity.db'}",
        "session_secret": "secret",
        "secure_cookies": False,
        "ollama_url": "http://invalid",
        "ollama_model": "test",
        "max_users": 10,
        "max_upload_mb": 2,
    }
    values.update(changes)
    return Settings(**values)


def test_admin_bootstrap_promotes_without_replacing_password(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        default_admin_email="OWNER@example.com",
        default_admin_password="temporary-bootstrap-password",
    )
    database = Database(settings)
    database.create_all()
    original_hash = hash_password("original-account-password")
    sender = MemoryEmailSender()
    with database.session_factory() as session:
        session.add(
            User(
                username="owner@example.com",
                display_name="Owner",
                email="owner@example.com",
                screen_name="Owner",
                password_hash=original_hash,
            )
        )
        session.commit()
        asyncio.run(bootstrap_admin(session, settings, sender))
        user = session.scalar(select(User).where(User.email == "owner@example.com"))
        assert user.is_admin
        assert verify_password("original-account-password", user.password_hash)
        assert not verify_password("temporary-bootstrap-password", user.password_hash)
        assert user.email_verified_at is None
        assert len(sender.messages) == 1
        asyncio.run(bootstrap_admin(session, settings, sender))
        assert len(sender.messages) == 1
    database.engine.dispose()


def test_production_identity_configuration_rejects_unsafe_proxy(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        app_env="production",
        public_base_url="https://bourbonbook.example.com",
        secure_cookies=True,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    with pytest.raises(ValueError, match="restricted FORWARDED_ALLOW_IPS"):
        settings.validate_identity()


def test_admin_bootstrap_requires_credentials_in_production_and_creates_fresh_admin(
    tmp_path: Path,
) -> None:
    production = settings_for(tmp_path, app_env="production")
    database = Database(production)
    database.create_all()
    with (
        database.session_factory() as session,
        pytest.raises(RuntimeError, match="DEFAULT_ADMIN_EMAIL"),
    ):
        asyncio.run(bootstrap_admin(session, production, MemoryEmailSender()))
    database.engine.dispose()

    configured = settings_for(
        tmp_path,
        default_admin_email="fresh@example.com",
        default_admin_password="temporary-admin-password",
    )
    database = Database(configured)
    sender = MemoryEmailSender()
    with database.session_factory() as session:
        admin = asyncio.run(bootstrap_admin(session, configured, sender))
        assert admin.email == "fresh@example.com"
        assert admin.is_admin
        assert len(sender.messages) == 1
    database.engine.dispose()
