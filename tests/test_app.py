from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, text

from bourbonbook.config import Settings
from bourbonbook.main import create_app
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import Bottle, User


def make_client(tmp_path: Path) -> tuple[TestClient, object]:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret-that-is-long-enough!",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=10,
        max_upload_mb=2,
    )
    bootstrap_database(settings)
    app = create_app(settings)
    return TestClient(app), app


def csrf(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def register(client: TestClient, username: str = "aaron") -> None:
    email = f"{username}@example.com"
    response = client.get("/register")
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf(response),
            "screen_name": "Aaron",
            "email": email,
            "password": "correct-horse-battery",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/check-email"
    message = client.app.state.email_sender.messages[-1]
    verification_url = re.search(r"https?://\S+", message.text).group(0)
    parsed = urlsplit(verification_url)
    staged = client.get(f"{parsed.path}?{parsed.query}", follow_redirects=False)
    assert staged.headers["location"] == "/verify-email/confirm"
    confirmation = client.get(staged.headers["location"])
    verified = client.post(
        "/verify-email/confirm",
        data={"csrf_token": csrf(confirmation)},
        follow_redirects=False,
    )
    assert verified.headers["location"] == "/profile"


def test_health_and_auth_redirect(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ok"}
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_readyz_reports_unready_when_database_is_not_at_head(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        with app.state.database.engine.begin() as connection:
            connection.execute(
                text("update alembic_version set version_num = '0001_current_schema'")
            )
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}


def test_registration_library_and_logout(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        register(client)
        library = client.get("/")
        assert library.status_code == 200
        assert "My Collection" in library.text
        assert "Your shelf is waiting" in library.text
        response = client.post(
            "/logout", data={"csrf_token": csrf(library)}, follow_redirects=False
        )
        assert response.status_code == 303
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_bottles_are_scoped_to_current_user(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client)
        with app.state.database.session_factory() as session:
            owner = session.scalar(select(User).where(User.email == "aaron@example.com"))
            session.add(Bottle(owner_id=owner.id, name="Eagle Rare", brand="Eagle Rare"))
            session.commit()
        assert "Eagle Rare" in client.get("/").text

        library = client.get("/")
        client.post("/logout", data={"csrf_token": csrf(library)})
        register(client, "someone_else")
        assert "Eagle Rare" not in client.get("/").text


def test_rejects_bad_csrf(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/register",
            data={
                "csrf_token": "wrong",
                "email": "aaron@example.com",
                "password": "long-password",
            },
        )
        assert response.status_code == 403


def test_add_review_edit_and_view_bottle(tmp_path: Path, monkeypatch) -> None:
    async def fake_analysis(photo, settings):
        return (
            {
                "name": "Eagle Rare 10 Year",
                "brand": "Eagle Rare",
                "spirit_type": "Bourbon",
                "proof": 90.0,
                "abv": 45.0,
                "size": "750ml",
            },
            "complete",
        )

    monkeypatch.setattr("bourbonbook.main.analyze_bottle", fake_analysis)
    client, _ = make_client(tmp_path)
    with client:
        register(client)
        new_page = client.get("/bottles/new")
        image_bytes = BytesIO()
        Image.new("RGB", (120, 200), "#7a3f1c").save(image_bytes, "PNG")
        response = client.post(
            "/bottles",
            data={
                "csrf_token": csrf(new_page),
                "purchase_price": "45.00",
                "quantity": "3",
            },
            files={"photo": ("bottle.png", image_bytes.getvalue(), "image/png")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].endswith("/edit?new=1")

        edit_page = client.get(response.headers["location"])
        assert "Label analysis complete" in edit_page.text
        assert "Eagle Rare 10 Year" in edit_page.text
        assert 'name="quantity" type="number" min="1" max="99" value="3"' in edit_page.text
        bottle_id = int(response.headers["location"].split("/")[2])

        refresh_photo = client.post(
            f"/bottles/{bottle_id}/analyze",
            data={
                "csrf_token": csrf(edit_page),
                "analysis_mode": "photo",
                "name": "Eagle Rare 10 Year",
                "brand": "Eagle Rare",
                "spirit_type": "Bourbon",
                "size": "750ml",
                "status": "Unopened",
                "fill_level": "100",
                "quantity": "3",
                "purchase_price": "45",
            },
            follow_redirects=False,
        )
        assert refresh_photo.status_code == 303
        assert refresh_photo.headers["location"].endswith("?analysis=complete")
        refreshed_page = client.get(refresh_photo.headers["location"])
        assert "Bottle details updated" in refreshed_page.text

        async def fake_name_analysis(name, settings):
            assert name == "Eagle Rare Kentucky Straight Bourbon"
            return ({"brand": "Eagle Rare", "distilled_by": "Buffalo Trace Distillery"}, "complete")

        monkeypatch.setattr("bourbonbook.main.analyze_bottle_name", fake_name_analysis)
        refresh_name = client.post(
            f"/bottles/{bottle_id}/analyze",
            data={
                "csrf_token": csrf(refreshed_page),
                "analysis_mode": "name",
                "name": "Eagle Rare Kentucky Straight Bourbon",
                "brand": "Eagle Rare",
                "spirit_type": "Bourbon",
                "size": "750ml",
                "status": "Unopened",
                "fill_level": "100",
                "quantity": "3",
                "purchase_price": "45",
            },
            follow_redirects=False,
        )
        assert refresh_name.status_code == 303
        name_page = client.get(refresh_name.headers["location"])
        assert "Buffalo Trace Distillery" in name_page.text

        save = client.post(
            f"/bottles/{bottle_id}/edit",
            data={
                "csrf_token": csrf(name_page),
                "name": "Eagle Rare 10 Year",
                "brand": "Eagle Rare",
                "spirit_type": "Bourbon",
                "proof": "90",
                "abv": "45",
                "size": "750ml",
                "status": "Opened",
                "fill_level": "40",
                "quantity": "2",
                "purchase_price": "45",
                "msrp": "49.99",
                "secondary_price": "100",
                "rating": "5",
                "tasting_notes": "Oak and orange peel",
            },
            follow_redirects=False,
        )
        assert save.status_code == 303
        detail = client.get(save.headers["location"])
        assert detail.status_code == 200
        assert "Oak and orange peel" in detail.text
        assert "$200.00" in detail.text
        photo_match = re.search(r"/media/([^\"]+)", detail.text)
        assert photo_match
        assert client.get("/media/" + photo_match.group(1)).status_code == 200
