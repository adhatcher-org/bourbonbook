from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from sqlalchemy import select

from bourbonbook.models import User
from tests.test_app import csrf, make_client, register


def test_password_change_invalidates_every_session(tmp_path: Path) -> None:
    first, app = make_client(tmp_path)
    second = TestClient(app)
    with first, second:
        register(first, "profile")
        login = second.get("/login")
        second.post(
            "/login",
            data={
                "csrf_token": csrf(login),
                "email": "profile@example.com",
                "password": "correct-horse-battery",
            },
        )
        profile = first.get("/profile")
        changed = first.post(
            "/profile/password",
            data={
                "csrf_token": csrf(profile),
                "current_password": "correct-horse-battery",
                "new_password": "another-correct-password",
                "password_confirmation": "another-correct-password",
            },
            follow_redirects=False,
        )
        assert changed.headers["location"] == "/login"
        assert first.get("/", follow_redirects=False).headers["location"] == "/login"
        assert second.get("/", follow_redirects=False).headers["location"] == "/login"


def test_profile_posts_require_csrf(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        register(client, "csrfprofile")
        assert client.post("/profile/name", data={"screen_name": "Nope"}).status_code == 403


def test_email_change_invalidates_other_session_until_reverified(tmp_path: Path) -> None:
    first, app = make_client(tmp_path)
    second = TestClient(app)
    with first, second:
        register(first, "oldemail")
        login = second.get("/login")
        second.post(
            "/login",
            data={
                "csrf_token": csrf(login),
                "email": "oldemail@example.com",
                "password": "correct-horse-battery",
            },
        )
        profile = first.get("/profile")
        changed = first.post(
            "/profile/email",
            data={
                "csrf_token": csrf(profile),
                "email": "NEWEMAIL@example.com",
                "current_password": "correct-horse-battery",
            },
            follow_redirects=False,
        )
        assert changed.headers["location"] == "/check-email"
        assert first.get("/", follow_redirects=False).headers["location"] == "/check-email"
        assert second.get("/", follow_redirects=False).headers["location"] == "/login"
        with app.state.database.session_factory() as session:
            user = session.scalar(select(User).where(User.email == "newemail@example.com"))
            assert user.email_verified_at is None

        url = re.search(r"https?://\S+", app.state.email_sender.messages[-1].text).group(0)
        parsed = urlsplit(url)
        staged = first.get(f"{parsed.path}?{parsed.query}", follow_redirects=False)
        confirmation = first.get(staged.headers["location"])
        assert (
            first.post(
                "/verify-email/confirm",
                data={"csrf_token": csrf(confirmation)},
                follow_redirects=False,
            ).headers["location"]
            == "/profile"
        )
