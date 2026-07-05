from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from bourbonbook.admin_config import CONFIG_FIELDS, read_managed_config, settings_values
from bourbonbook.auth import hash_password
from bourbonbook.config import Settings
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
        assert client.get("/admin/config").status_code == 403


def config_form(app, **changes: str) -> dict[str, str]:
    values = settings_values(app.state.settings)
    for field in CONFIG_FIELDS:
        if field.secret:
            values[field.key] = ""
    values.update(changes)
    return values


def test_admin_can_save_validated_configuration_and_secrets_are_masked(
    tmp_path: Path, monkeypatch
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")

        page = client.get("/admin/config")
        assert page.status_code == 200
        assert "Analysis provider" in page.text
        assert app.state.settings.session_secret not in page.text

        response = client.post(
            "/admin/config",
            data={
                **config_form(app, ANALYSIS_PROVIDER="openai", OPENAI_MODEL="gpt-test"),
                "csrf_token": csrf(page),
                "OPENAI_API_KEY": "sk-test-secret",
            },
        )
        assert response.status_code == 200
        assert "Configuration saved" in response.text

    stored = read_managed_config(tmp_path / ".env")
    assert stored["ANALYSIS_PROVIDER"] == "openai"
    assert stored["OPENAI_API_KEY"] == "sk-test-secret"
    assert stored["SESSION_SECRET"] == app.state.settings.session_secret
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reloaded = Settings.from_env()
    assert reloaded.analysis_provider == "openai"
    assert reloaded.openai_model == "gpt-test"


def test_admin_config_rejects_invalid_choices_without_writing(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        page = client.get("/admin/config")
        response = client.post(
            "/admin/config",
            data={
                **config_form(app, ANALYSIS_PROVIDER="other"),
                "csrf_token": csrf(page),
            },
        )

        assert response.status_code == 400
        assert "ANALYSIS_PROVIDER must be one of" in response.text
        assert not (tmp_path / ".env").exists()


def test_admin_config_cannot_override_deployment_data_directory(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        page = client.get("/admin/config")
        response = client.post(
            "/admin/config",
            data={
                **config_form(app),
                "csrf_token": csrf(page),
                "DATA_DIR": "/etc",
            },
        )

        assert response.status_code == 200
        assert app.state.settings.data_dir == tmp_path
        assert "DATA_DIR=" not in (tmp_path / ".env").read_text(encoding="utf-8")


def test_admin_can_request_process_restart(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    restarted = []
    app.state.restart = lambda: restarted.append(True)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        page = client.get("/admin/config")
        response = client.post(
            "/admin/restart", data={"csrf_token": csrf(page)}, follow_redirects=False
        )

        assert response.status_code == 200
        assert "restarting" in response.text.lower()
        assert restarted == [True]


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
