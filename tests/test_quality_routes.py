from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from bourbonbook.auth import hash_password
from bourbonbook.models import Bottle, PriceSource, User
from tests.test_app import csrf, make_client, register


def post_with_csrf(client: TestClient, path: str, page_path: str, data: dict[str, str]):
    page = client.get(page_path)
    return client.post(path, data={"csrf_token": csrf(page), **data}, follow_redirects=False)


def test_registration_validation_collision_limit_and_rate_limit(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        page = client.get("/register")
        invalid = client.post(
            "/register",
            data={
                "csrf_token": csrf(page),
                "email": "not-email",
                "screen_name": "x" * 81,
                "password": "short",
            },
        )
        assert "Screen name must be 80 characters or fewer" in invalid.text

        register(client, "existing")
        profile = client.get("/profile")
        client.post("/logout", data={"csrf_token": csrf(profile)})
        page = client.get("/register")
        duplicate = client.post(
            "/register",
            data={
                "csrf_token": csrf(page),
                "email": "existing@example.com",
                "password": "correct-horse-battery",
            },
        )
        assert "already in use" in duplicate.text

        object.__setattr__(app.state.settings, "max_users", 1)
        page = client.get("/register")
        limited = client.post(
            "/register",
            data={
                "csrf_token": csrf(page),
                "email": "another@example.com",
                "password": "correct-horse-battery",
            },
        )
        assert "reached its user limit" in limited.text

        app.state.rate_limiter.allow = lambda *_args: False
        page = client.get("/register")
        throttled = client.post(
            "/register",
            data={
                "csrf_token": csrf(page),
                "email": "rate@example.com",
                "password": "correct-horse-battery",
            },
        )
        assert throttled.status_code == 429


def test_login_failure_unverified_redirect_and_authenticated_redirect(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        page = client.get("/login")
        failure = client.post(
            "/login",
            data={"csrf_token": csrf(page), "email": "bad", "password": "wrong"},
        )
        assert "Email or password is incorrect" in failure.text

        with app.state.database.session_factory() as session:
            session.add(
                User(
                    username="waiting@example.com",
                    display_name="Waiting",
                    email="waiting@example.com",
                    screen_name="Waiting",
                    password_hash=hash_password("correct-horse-battery"),
                )
            )
            session.commit()
        page = client.get("/login")
        waiting = client.post(
            "/login",
            data={
                "csrf_token": csrf(page),
                "email": "waiting@example.com",
                "password": "correct-horse-battery",
            },
            follow_redirects=False,
        )
        assert waiting.headers["location"] == "/check-email"

        register(client, "signedin")
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_profile_validation_and_updates(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "profilechecks")
        response = post_with_csrf(client, "/profile/name", "/profile", {"screen_name": ""})
        assert "Screen name must be 1–80 characters" in response.text
        response = post_with_csrf(
            client, "/profile/name", "/profile", {"screen_name": "Updated Owner"}
        )
        assert "Screen name updated" in response.text

        with app.state.database.session_factory() as session:
            session.add(
                User(
                    username="taken@example.com",
                    display_name="Taken",
                    email="taken@example.com",
                    screen_name="Taken",
                    email_verified_at=datetime.now(UTC),
                    password_hash=hash_password("correct-horse-battery"),
                )
            )
            session.commit()

        wrong = post_with_csrf(
            client,
            "/profile/email",
            "/profile",
            {"current_password": "wrong", "email": "new@example.com"},
        )
        assert "Current password is incorrect" in wrong.text
        invalid = post_with_csrf(
            client,
            "/profile/email",
            "/profile",
            {"current_password": "correct-horse-battery", "email": "invalid"},
        )
        assert "valid email" in invalid.text
        duplicate = post_with_csrf(
            client,
            "/profile/email",
            "/profile",
            {"current_password": "correct-horse-battery", "email": "taken@example.com"},
        )
        assert "already in use" in duplicate.text

        wrong_password = post_with_csrf(
            client,
            "/profile/password",
            "/profile",
            {
                "current_password": "wrong",
                "new_password": "replacement-password",
                "password_confirmation": "replacement-password",
            },
        )
        assert "Current password is incorrect" in wrong_password.text
        mismatch = post_with_csrf(
            client,
            "/profile/password",
            "/profile",
            {
                "current_password": "correct-horse-battery",
                "new_password": "replacement-password",
                "password_confirmation": "different-password",
            },
        )
        assert "Passwords do not match" in mismatch.text
        short = post_with_csrf(
            client,
            "/profile/password",
            "/profile",
            {
                "current_password": "correct-horse-battery",
                "new_password": "short",
                "password_confirmation": "short",
            },
        )
        assert "at least 10 characters" in short.text


def test_profile_avatar_upload_render_and_remove(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    source = BytesIO()
    Image.new("RGB", (900, 600), "#8b4513").save(source, "PNG")
    with client:
        register(client, "avatarowner")
        profile = client.get("/profile")
        uploaded = client.post(
            "/profile/avatar",
            data={"csrf_token": csrf(profile)},
            files={"avatar": ("portrait.png", source.getvalue(), "image/png")},
        )
        assert uploaded.status_code == 200
        assert "Avatar updated" in uploaded.text

        with app.state.database.session_factory() as session:
            owner = session.scalar(select(User).where(User.email == "avatarowner@example.com"))
            avatar_name = owner.avatar_name
        assert avatar_name
        avatar_path = tmp_path / "avatars" / avatar_name
        with Image.open(avatar_path) as avatar:
            assert avatar.size == (512, 512)

        library = client.get("/")
        assert f'src="/avatars/{avatar_name}"' in library.text
        avatar_response = client.get(f"/avatars/{avatar_name}")
        assert avatar_response.status_code == 200
        assert avatar_response.headers["content-type"] == "image/jpeg"

        removed = client.post(
            "/profile/avatar/remove",
            data={"csrf_token": csrf(uploaded)},
        )
        assert "Avatar removed" in removed.text
        assert not avatar_path.exists()


def test_profile_avatar_rejects_missing_and_invalid_uploads(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        register(client, "badavatar")
        profile = client.get("/profile")
        missing = client.post(
            "/profile/avatar",
            data={"csrf_token": csrf(profile)},
        )
        assert "Choose an image" in missing.text
        invalid = client.post(
            "/profile/avatar",
            data={"csrf_token": csrf(profile)},
            files={"avatar": ("avatar.jpg", b"not an image", "image/jpeg")},
        )
        assert invalid.status_code == 400
        assert "valid image" in invalid.text


def test_profile_deletion_removes_catalog_photo_and_avatar(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "deleteowner")
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir(exist_ok=True)
        photo = upload_dir / "owned.jpg"
        photo.write_bytes(b"photo")
        avatar_dir = tmp_path / "avatars"
        avatar_dir.mkdir(exist_ok=True)
        avatar = avatar_dir / "avatar.jpg"
        avatar.write_bytes(b"avatar")
        with app.state.database.session_factory() as session:
            owner = session.scalar(select(User).where(User.email == "deleteowner@example.com"))
            owner.avatar_name = avatar.name
            session.add(Bottle(owner_id=owner.id, name="Owned", photo_name=photo.name))
            session.commit()

        invalid = post_with_csrf(
            client,
            "/profile/delete",
            "/profile",
            {"current_password": "wrong", "confirmation": "DELETE MY ACCOUNT"},
        )
        assert "exact confirmation phrase" in invalid.text
        deleted = post_with_csrf(
            client,
            "/profile/delete",
            "/profile",
            {
                "current_password": "correct-horse-battery",
                "confirmation": "DELETE MY ACCOUNT",
            },
        )
        assert deleted.headers["location"] == "/account-deleted"
        assert not photo.exists()
        assert not avatar.exists()
        assert "Account deleted" in client.get("/account-deleted").text


def test_bottle_missing_edit_sources_delete_and_media(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "catalogchecks")
        assert client.get("/manifest.webmanifest").status_code == 200
        assert client.get("/bottles/999", follow_redirects=False).headers["location"] == "/"
        assert client.get("/bottles/999/edit", follow_redirects=False).headers["location"] == "/"

        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir(exist_ok=True)
        photo = upload_dir / "bottle.jpg"
        photo.write_bytes(b"jpeg-ish")
        with app.state.database.session_factory() as session:
            owner = session.scalar(select(User).where(User.email == "catalogchecks@example.com"))
            bottle = Bottle(
                owner_id=owner.id,
                name="Before",
                photo_name=photo.name,
                purchase_price=55,
                msrp=50,
                quantity=2,
                secondary_price=80,
            )
            bottle.price_sources.extend(
                [
                    PriceSource(kind="msrp", title="MSRP", url="https://example.com/msrp"),
                    PriceSource(kind="secondary", title="Market", url="https://example.com/market"),
                ]
            )
            session.add(bottle)
            session.commit()
            bottle_id = bottle.id

        detail = client.get(f"/bottles/{bottle_id}")
        assert detail.status_code == 200
        assert "Secondary" not in detail.text
        assert "Stored at" not in detail.text
        assert "Grounded price source" not in detail.text
        assert "https://example.com/msrp" not in detail.text
        assert detail.text.index("Basic information") < detail.text.index("Valuation &amp; status")
        assert "Barrel information" not in detail.text
        assert "Before" in client.get("/?q=Before&sort=name").text
        assert client.get(f"/media/{photo.name}").status_code == 200
        edit = client.get(f"/bottles/{bottle_id}/edit?new=1&analysis=complete")
        assert 'name="secondary_price"' not in edit.text
        assert 'name="storage_location"' not in edit.text
        assert "Look up internet pricing" in edit.text
        assert "OHLQ" in edit.text
        assert edit.text.index('name="purchase_price"') < edit.text.index('name="quantity"')
        assert 'value="$110.00" readonly data-total-spent' in edit.text
        assert 'value="$100.00" readonly data-total-value' in edit.text
        assert 'class="msrp-field"' in edit.text
        assert 'class="price-refresh price-refresh-inline"' in edit.text
        assert "Barrel information" not in edit.text
        saved = client.post(
            f"/bottles/{bottle_id}/edit",
            data={
                "csrf_token": csrf(edit),
                "name": "After",
                "msrp": "55",
                "secondary_price": "80",
                "status": "invalid",
                "quantity": "999",
                "rating": "bad",
            },
            follow_redirects=False,
        )
        assert saved.headers["location"] == f"/bottles/{bottle_id}"
        with app.state.database.session_factory() as session:
            bottle = session.get(Bottle, bottle_id)
            assert bottle.status == "Unopened"
            assert bottle.quantity == 99
            assert {source.kind for source in bottle.price_sources} == {"secondary"}
            bottle.barrel_number = "A-107"
            session.commit()

        barrel_edit = client.get(f"/bottles/{bottle_id}/edit")
        assert "Barrel information" in barrel_edit.text
        assert 'class="form-section collapsible-section" open' in barrel_edit.text

        deleted = client.post(
            f"/bottles/{bottle_id}/delete",
            data={"csrf_token": csrf(edit)},
            follow_redirects=False,
        )
        assert deleted.headers["location"] == "/"
        assert not photo.exists()
        assert client.get(f"/media/{photo.name}").status_code == 404


def test_resend_forgot_and_reset_validation_paths(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        registration = client.get("/register")
        client.post(
            "/register",
            data={
                "csrf_token": csrf(registration),
                "email": "waiting@example.com",
                "password": "correct-horse-battery",
            },
        )
        check_email = client.get("/check-email")
        before = len(app.state.email_sender.messages)
        resent = client.post(
            "/verification/resend",
            data={"csrf_token": csrf(check_email), "email": "waiting@example.com"},
        )
        assert resent.status_code == 200
        assert len(app.state.email_sender.messages) == before + 1

        forgot = client.get("/forgot-password")
        form_token = csrf(forgot)
        invalid_email = client.post(
            "/forgot-password", data={"csrf_token": form_token, "email": "invalid"}
        )
        assert "If that email belongs to an account" in invalid_email.text

        client.get("/reset-password")
        mismatch = client.post(
            "/reset-password",
            data={
                "csrf_token": form_token,
                "password": "a-long-password",
                "password_confirmation": "different-password",
            },
        )
        assert "Passwords do not match" in mismatch.text
        short = client.post(
            "/reset-password",
            data={
                "csrf_token": csrf(mismatch),
                "password": "short",
                "password_confirmation": "short",
            },
        )
        assert "at least 10 characters" in short.text
        missing_token = client.post(
            "/reset-password",
            data={
                "csrf_token": csrf(short),
                "password": "a-long-password",
                "password_confirmation": "a-long-password",
            },
        )
        assert missing_token.status_code == 400
        assert "invalid or expired" in missing_token.text
