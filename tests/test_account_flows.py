from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import select

from bourbonbook.models import User
from tests.test_app import csrf, make_client, register


def message_path(message) -> str:
    url = re.search(r"https?://\S+", message.text).group(0)
    parsed = urlsplit(url)
    return f"{parsed.path}?{parsed.query}"


def test_scanner_get_does_not_verify_or_consume_token(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    scanner = TestClient(app)
    with client, scanner:
        page = client.get("/register")
        response = client.post(
            "/register",
            data={
                "csrf_token": csrf(page),
                "email": "scan@example.com",
                "screen_name": "Scanner",
                "password": "correct-horse-battery",
            },
            follow_redirects=False,
        )
        assert response.headers["location"] == "/check-email"
        link = message_path(app.state.email_sender.messages[-1])
        scanner_confirmation = scanner.get(link, follow_redirects=True)
        scanner_csrf = csrf(scanner_confirmation)
        with app.state.database.session_factory() as session:
            assert (
                session.scalar(
                    select(User).where(User.email == "scan@example.com")
                ).email_verified_at
                is None
            )

        staged = client.get(link, follow_redirects=False)
        confirmation = client.get(staged.headers["location"])
        result = client.post(
            "/verify-email/confirm",
            data={"csrf_token": csrf(confirmation)},
            follow_redirects=False,
        )
        assert result.headers["location"] == "/profile"

        assert (
            "invalid or expired"
            in scanner.post(
                "/verify-email/confirm",
                data={"csrf_token": scanner_csrf},
            ).text
        )


def test_password_reset_is_generic_and_invalidates_old_sessions(tmp_path: Path) -> None:
    owner, app = make_client(tmp_path)
    second = TestClient(app)
    with owner, second:
        register(owner, "reset")
        login = second.get("/login")
        assert (
            second.post(
                "/login",
                data={
                    "csrf_token": csrf(login),
                    "email": "reset@example.com",
                    "password": "correct-horse-battery",
                },
                follow_redirects=False,
            ).headers["location"]
            == "/"
        )

        forgot = owner.get("/forgot-password")
        known = owner.post(
            "/forgot-password",
            data={"csrf_token": csrf(forgot), "email": "reset@example.com"},
        )
        unknown_page = TestClient(app).get("/forgot-password")
        unknown_client = TestClient(app)
        with unknown_client:
            unknown_page = unknown_client.get("/forgot-password")
            unknown = unknown_client.post(
                "/forgot-password",
                data={"csrf_token": csrf(unknown_page), "email": "missing@example.com"},
            )
        assert "If that email belongs to an account" in known.text
        assert "If that email belongs to an account" in unknown.text

        link = message_path(app.state.email_sender.messages[-1])
        staged = owner.get(link, follow_redirects=False)
        form = owner.get(staged.headers["location"])
        reset = owner.post(
            "/reset-password",
            data={
                "csrf_token": csrf(form),
                "password": "a-brand-new-password",
                "password_confirmation": "a-brand-new-password",
            },
            follow_redirects=False,
        )
        assert reset.headers["location"] == "/login"
        assert second.get("/", follow_redirects=False).headers["location"] == "/login"

        login = owner.get("/login")
        assert (
            owner.post(
                "/login",
                data={
                    "csrf_token": csrf(login),
                    "email": "reset@example.com",
                    "password": "a-brand-new-password",
                },
                follow_redirects=False,
            ).headers["location"]
            == "/"
        )
