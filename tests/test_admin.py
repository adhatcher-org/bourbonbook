from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from bourbonbook.auth import hash_password
from bourbonbook.models import ApiUsage, User
from tests.test_app import csrf, make_client, register


def promote_admin(app, email: str) -> int:
    with app.state.database.session_factory() as session:
        user = session.query(User).filter_by(email=email).one()
        user.is_admin = True
        session.commit()
        return user.id


def add_verified_user(app, email: str = "target@example.com") -> int:
    with app.state.database.session_factory() as session:
        user = User(
            username=email,
            display_name="Target",
            email=email,
            screen_name="Target",
            email_verified_at=datetime.now(UTC),
            password_hash=hash_password("correct-horse-battery"),
        )
        session.add(user)
        session.commit()
        return user.id


def test_admin_routes_require_admin(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        register(client)
        assert client.get("/admin/users").status_code == 403


def test_admin_email_correction_invalidates_session_and_sends_verification(
    tmp_path: Path,
) -> None:
    admin_client, app = make_client(tmp_path)
    target_client = TestClient(app)
    with admin_client, target_client:
        register(admin_client, "admin")
        promote_admin(app, "admin@example.com")
        target_id = add_verified_user(app)

        login_page = target_client.get("/login")
        assert (
            target_client.post(
                "/login",
                data={
                    "csrf_token": csrf(login_page),
                    "email": "target@example.com",
                    "password": "correct-horse-battery",
                },
                follow_redirects=False,
            ).headers["location"]
            == "/"
        )

        detail = admin_client.get(f"/admin/users/{target_id}")
        response = admin_client.post(
            f"/admin/users/{target_id}/email",
            data={
                "csrf_token": csrf(detail),
                "email": "new-target@example.com",
                "confirmation": "new-target@example.com",
            },
        )

        assert response.status_code == 200
        assert "Email changed and verification sent" in response.text
        assert app.state.email_sender.messages[-1].recipient == "new-target@example.com"
        assert target_client.get("/", follow_redirects=False).headers["location"] == "/login"

        with app.state.database.session_factory() as session:
            target = session.get(User, target_id)
            assert target.email == "new-target@example.com"
            assert target.email_verified_at is None


def test_admin_user_list_search_and_safe_actions(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        target_id = add_verified_user(app)
        duplicate_id = add_verified_user(app, "duplicate@example.com")

        listing = client.get("/admin/users?q=target")
        assert listing.status_code == 200
        assert "target@example.com" in listing.text

        missing = client.get("/admin/users/999", follow_redirects=False)
        assert missing.headers["location"] == "/admin/users"

        detail = client.get(f"/admin/users/{target_id}")
        reset = client.post(
            f"/admin/users/{target_id}/send-reset",
            data={"csrf_token": csrf(detail)},
        )
        assert "Password reset email sent" in reset.text

        detail = client.get(f"/admin/users/{target_id}")
        verification = client.post(
            f"/admin/users/{target_id}/resend-verification",
            data={"csrf_token": csrf(detail)},
        )
        assert "Verification email sent" in verification.text

        detail = client.get(f"/admin/users/{target_id}")
        bad_email = client.post(
            f"/admin/users/{target_id}/email",
            data={"csrf_token": csrf(detail), "email": "not-an-email", "confirmation": "x"},
        )
        assert bad_email.status_code == 400
        assert "valid email" in bad_email.text

        detail = client.get(f"/admin/users/{target_id}")
        mismatch = client.post(
            f"/admin/users/{target_id}/email",
            data={
                "csrf_token": csrf(detail),
                "email": "fresh@example.com",
                "confirmation": "different@example.com",
            },
        )
        assert mismatch.status_code == 400
        assert "exactly" in mismatch.text

        detail = client.get(f"/admin/users/{target_id}")
        duplicate = client.post(
            f"/admin/users/{target_id}/email",
            data={
                "csrf_token": csrf(detail),
                "email": "duplicate@example.com",
                "confirmation": "duplicate@example.com",
            },
        )
        assert duplicate.status_code == 400
        assert "already in use" in duplicate.text

        no_target = client.post(
            "/admin/users/999/email",
            data={
                "csrf_token": csrf(detail),
                "email": "nobody@example.com",
                "confirmation": "nobody@example.com",
            },
            follow_redirects=False,
        )
        assert no_target.headers["location"] == "/admin/users"
        assert duplicate_id != target_id


def test_admin_usage_totals_are_visible_to_admin(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        with app.state.database.session_factory() as session:
            session.add(
                ApiUsage(
                    provider="openai",
                    operation="price_search",
                    model="gpt-test",
                    success=True,
                    duration_ms=250,
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                )
            )
            session.commit()

        response = client.get("/admin/usage")
        assert response.status_code == 200
        assert "price_search" in response.text
        assert "gpt-test" in response.text
        assert "15" in response.text
