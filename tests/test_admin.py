from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import Request

from bourbonbook.admin_config import CONFIG_FIELDS, read_managed_config, settings_values
from bourbonbook.auth import hash_password
from bourbonbook.catalog_import_worker import claim_next_catalog_import
from bourbonbook.catalog_imports import (
    CatalogImportApplyStateError,
    apply_catalog_import_batch,
)
from bourbonbook.catalog_uploads import catalog_import_batch_directory
from bourbonbook.config import Settings
from bourbonbook.main import delete_catalog_import_batch, enforce_catalog_import_request_size
from bourbonbook.models import (
    ApiUsage,
    CatalogImportBatch,
    CatalogImportProposal,
    CatalogPrice,
    User,
)
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


def test_admin_catalog_lists_filters_updates_and_deletes_prices(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        with app.state.database.session_factory() as session:
            prices = [
                CatalogPrice(
                    product_key=f"example bourbon {number:02}",
                    size_key="750ml",
                    msrp=number,
                    url="",
                    checked_at=datetime(2026, 7, 21, tzinfo=UTC),
                )
                for number in range(1, 27)
            ]
            session.add_all(prices)
            session.commit()
            update_id, delete_id = prices[0].id, prices[1].id

        listing = client.get("/admin/catalog?q=example&sort=price_desc&page=9&page_size=10")
        assert listing.status_code == 200
        assert "example bourbon 06" in listing.text
        assert "example bourbon 01" in listing.text
        assert "example bourbon 26" not in listing.text
        assert "<th>Size</th>" in listing.text
        assert "<th>Price date</th>" in listing.text
        assert "750ml" in listing.text
        assert "2026-07-21" in listing.text
        assert 'class="catalog-tools catalog-price-toolbar"' in listing.text
        assert 'class="admin-table catalog-price-table"' in listing.text
        assert 'class="catalog-name-input"' in listing.text
        assert 'class="catalog-price-input"' in listing.text
        styles = Path("bourbonbook/static/app.css").read_text()
        assert (
            ".catalog-price-table th:nth-child(2),.catalog-price-table td:nth-child(2){width:48%}"
            in styles
        )
        assert ".catalog-price-table .catalog-name-input{width:100%;min-width:0}" in styles
        assert client.get("/admin/catalog?sort=unknown&page_size=7").status_code == 200

        response = client.post(
            "/admin/catalog",
            data={
                "csrf_token": csrf(listing),
                "q": "example",
                "sort": "name_desc",
                "page": "1",
                "page_size": "10",
                "action": "update",
                f"name_{update_id}": "Updated Bourbon",
                f"msrp_{update_id}": "77.25",
                "name_999999": "Missing",
                f"name_{delete_id}": "",
                f"msrp_{delete_id}": "0",
            },
            follow_redirects=False,
        )
        assert response.headers["location"] == (
            "/admin/catalog?q=example&sort=name_desc&page=1&page_size=10"
        )

        with app.state.database.session_factory() as session:
            updated = session.get(CatalogPrice, update_id)
            assert updated is not None
            assert (updated.product_key, updated.msrp) == ("updated bourbon", 77.25)

        delete_page = client.get("/admin/catalog")
        deleted = client.post(
            "/admin/catalog",
            data={
                "csrf_token": csrf(delete_page),
                "action": "delete",
                "selected": [str(delete_id), "999999"],
            },
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        with app.state.database.session_factory() as session:
            assert session.get(CatalogPrice, delete_id) is None


def catalog_png() -> bytes:
    from PIL import Image

    content = BytesIO()
    Image.new("RGB", (2, 2), "white").save(content, "PNG")
    return content.getvalue()


def test_admin_catalog_import_stages_validated_uploads(tmp_path: Path, monkeypatch) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")

        import bourbonbook.main as main

        async def provider_call(*_args) -> None:
            raise AssertionError("catalog staging must not invoke an analysis provider")

        async def qdrant_write(*_args) -> bool:
            raise AssertionError("catalog staging must not write Qdrant")

        monkeypatch.setattr(main, "analyze_bottle", provider_call)
        monkeypatch.setattr(main, "analyze_bottle_name", provider_call)
        monkeypatch.setattr(app.state.qdrant_price_index, "upsert", qdrant_write)

        page = client.get("/admin/catalog-import")
        assert page.status_code == 200
        invalid = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(page)},
        )
        assert invalid.status_code == 400
        assert "Upload PNG, JPEG, or PDF files" in invalid.text

        receipt_page = client.get("/admin/catalog-import")
        accepted = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(receipt_page)},
            files=[("pages", ("../../catalog.png", catalog_png(), "image/png"))],
        )
        assert accepted.status_code == 200
        assert "Extraction has been queued" in accepted.text
        with app.state.database.session_factory() as session:
            batch = session.query(CatalogImportBatch).one()
            assert (batch.created_by_user_id, batch.source_file_count, batch.state) == (
                1,
                1,
                "queued",
            )
            assert session.query(CatalogPrice).count() == 0
            source_directory = catalog_import_batch_directory(app.state.settings, batch.id)
        assert f"Batch #{batch.id}" in accepted.text
        assert "import-state-queued" in accepted.text
        assert "Recent import batches" in accepted.text
        staged_files = list(source_directory.iterdir())
        assert len(staged_files) == 1
        assert staged_files[0].suffix == ".png"
        assert "catalog" not in staged_files[0].name


def test_admin_catalog_import_post_requires_csrf_and_verified_admin(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        assert client.post("/admin/catalog-import").status_code == 403
        register(client, "member")
        profile = client.get("/profile")
        denied = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(profile)},
            files=[("pages", ("catalog.png", catalog_png(), "image/png"))],
        )
        assert denied.status_code == 403


def test_admin_catalog_import_rejects_unauthorized_requests_before_form_parsing(
    tmp_path: Path, monkeypatch
) -> None:
    anonymous_client, _ = make_client(tmp_path / "anonymous")
    member_client, _ = make_client(tmp_path / "member")
    with member_client:
        register(member_client, "member")

    form_calls = 0

    async def form_must_not_run(self, **_kwargs) -> None:
        nonlocal form_calls
        form_calls += 1
        raise AssertionError("unauthorized catalog imports must not parse multipart bodies")

    monkeypatch.setattr(Request, "form", form_must_not_run)
    with anonymous_client:
        anonymous = anonymous_client.post("/admin/catalog-import")
        assert anonymous.status_code == 403
    with member_client:
        non_admin = member_client.post("/admin/catalog-import")
        assert non_admin.status_code == 403
    assert form_calls == 0


def test_admin_catalog_import_rejects_bad_content_and_cleans_staging_failures(
    tmp_path: Path, monkeypatch
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        page = client.get("/admin/catalog-import")
        mismatch = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(page)},
            files=[("pages", ("catalog.png", b"not-a-real-image", "image/png"))],
        )
        assert mismatch.status_code == 400
        assert "does not match its type" in mismatch.text
        assert not (tmp_path / "catalog-imports").exists()

        import bourbonbook.main as main

        def fail_staging(*_args) -> None:
            raise OSError

        monkeypatch.setattr(main, "stage_catalog_uploads", fail_staging)
        page = client.get("/admin/catalog-import")
        failure = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(page)},
            files=[("pages", ("catalog.png", catalog_png(), "image/png"))],
        )
        assert failure.status_code == 500
        with app.state.database.session_factory() as session:
            assert session.query(CatalogImportBatch).count() == 0
        assert not list((tmp_path / "catalog-imports").glob("*"))


def test_admin_catalog_import_commit_failure_cleans_sources_and_skips_catalog_side_effects(
    tmp_path: Path, monkeypatch
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")

        def fail_commit(self) -> None:
            raise SQLAlchemyError("database unavailable")

        async def qdrant_write(*_args) -> bool:
            raise AssertionError("catalog staging must not write Qdrant")

        monkeypatch.setattr(Session, "commit", fail_commit)
        monkeypatch.setattr(app.state.qdrant_price_index, "upsert", qdrant_write)
        page = client.get("/admin/catalog-import")
        failure = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(page)},
            files=[("pages", ("catalog.png", catalog_png(), "image/png"))],
        )
        assert failure.status_code == 500
        with app.state.database.session_factory() as session:
            assert session.query(CatalogImportBatch).count() == 0
            assert session.query(CatalogPrice).count() == 0
        assert not list((tmp_path / "catalog-imports").glob("*"))


def test_admin_catalog_import_enforces_configured_batch_limits(tmp_path: Path, monkeypatch) -> None:
    client, app = make_client(tmp_path)
    app.state.settings = Settings(**{**vars(app.state.settings), "catalog_import_max_files": 1})
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        import bourbonbook.main as main

        def validation_must_not_run(*_args) -> None:
            raise AssertionError("multipart parser must reject excess files before validation")

        monkeypatch.setattr(main, "validate_catalog_uploads", validation_must_not_run)
        page = client.get("/admin/catalog-import")
        limited = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(page)},
            files=[
                ("pages", ("first.png", catalog_png(), "image/png")),
                ("pages", ("second.png", catalog_png(), "image/png")),
            ],
        )
        assert limited.status_code == 413
        assert "at most 1 catalog files" in limited.text


def test_admin_catalog_import_rejects_aggregate_request_before_form_parsing(
    tmp_path: Path, monkeypatch
) -> None:
    client, app = make_client(tmp_path)
    app.state.settings = Settings(**{**vars(app.state.settings), "catalog_import_max_total_mb": 1})
    form_calls = 0

    async def form_must_not_run(self, **_kwargs) -> None:
        nonlocal form_calls
        form_calls += 1
        raise AssertionError("oversized catalog import must be rejected before form parsing")

    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        page = client.get("/admin/catalog-import")
        monkeypatch.setattr(Request, "form", form_must_not_run)
        rejected = client.post(
            "/admin/catalog-import",
            data={"csrf_token": csrf(page)},
            files=[("pages", ("catalog.png", b"x" * (1024 * 1024), "image/png"))],
        )
        assert rejected.status_code == 413
        assert "exceeds the configured total size limit" in rejected.text
        assert form_calls == 0


def test_catalog_import_request_size_requires_valid_content_length() -> None:
    class RequestWithoutContentLength:
        headers: dict[str, str] = {}

    with pytest.raises(HTTPException, match="Content-Length") as missing:
        enforce_catalog_import_request_size(RequestWithoutContentLength(), 100)  # type: ignore[arg-type]
    assert missing.value.status_code == 411

    class RequestWithInvalidContentLength:
        headers = {"content-length": "not-a-number"}

    with pytest.raises(HTTPException, match="Invalid Content-Length") as invalid:
        enforce_catalog_import_request_size(RequestWithInvalidContentLength(), 100)  # type: ignore[arg-type]
    assert invalid.value.status_code == 400


def create_review_batch(app, user_id: int, proposal_count: int = 1) -> CatalogImportBatch:
    with app.state.database.session_factory() as session:
        batch = CatalogImportBatch(
            created_by_user_id=user_id,
            state="review",
            source_file_count=1,
        )
        session.add(batch)
        session.flush()
        session.add_all(
            CatalogImportProposal(
                batch_id=batch.id,
                position=position,
                included=True,
                name=f"Review Bourbon {position}",
                product_key=f"review bourbon {position}",
                size_key="750ml",
                msrp=float(position),
                price_updated_at=datetime(2026, 7, 22, tzinfo=UTC).date(),
            )
            for position in range(1, proposal_count + 1)
        )
        session.commit()
        return batch


def test_admin_catalog_import_review_lists_owned_batches_and_edits_proposals(
    tmp_path: Path,
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id, proposal_count=26)

        listing = client.get("/admin/catalog-import")
        assert listing.status_code == 200
        assert f"Batch #{batch.id}" in listing.text
        assert "Recent import batches" in listing.text
        assert f"/admin/catalog-import/{batch.id}" in listing.text
        assert "Batch #" + str(batch.id) + " review" not in listing.text
        assert "Review Bourbon 1" not in listing.text

        review = client.get(f"/admin/catalog-import/{batch.id}?page=2")
        assert review.status_code == 200
        assert "Back to catalog imports" in review.text
        assert "Review Bourbon 26" in review.text
        assert "Review Bourbon 1" not in review.text
        assert "Review Bourbon 25" not in review.text
        assert 'aria-label="Proposal pages"' in review.text
        assert 'for="name-' in review.text
        assert 'aria-label="Include proposal 26"' in review.text
        assert "Include proposal 26</label>" not in review.text
        assert 'class="proposal-name-column"' in review.text
        assert 'class="proposal-name-input"' in review.text
        assert 'class="proposal-size-input"' in review.text
        assert 'class="proposal-price-input"' in review.text
        assert 'class="proposal-size-input" name="size_' in review.text
        assert 'required maxlength="10"' in review.text
        styles = Path("bourbonbook/static/app.css").read_text()
        assert 'class="app-shell edit-page admin-page catalog-import-review-page"' in review.text
        assert 'id="catalog-import-review-form"' in review.text
        assert 'form="catalog-import-review-form">Save review changes</button>' in review.text
        assert 'class="review-primary-actions"' in review.text
        assert 'class="proposal-date-input"' in review.text
        assert ".catalog-import-review-page{width:min(1440px,calc(100% - 48px))}" in styles
        assert ".import-proposals{min-width:1280px;table-layout:fixed}" in styles
        assert ".import-proposals .proposal-include-column{width:72px}" in styles
        assert ".import-proposals th,.import-proposals td{padding:9px 12px}" in styles
        assert ".import-proposals input{width:100%;min-width:0" in styles
        assert "padding:7px 9px;outline:0}" in styles
        assert (
            ".import-proposals input[type=checkbox]{width:22px;height:22px;min-width:22px" in styles
        )
        assert ".import-proposals .proposal-size-input{width:100%;min-width:14ch}" in styles
        assert ".import-proposals .proposal-price-input{width:100%;min-width:11ch}" in styles
        assert (
            ".review-primary-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))"
            in styles
        )
        assert (
            "@media(max-width:620px){.catalog-import-review-page{width:calc(100% - 28px)}"
            ".review-primary-actions{grid-template-columns:1fr}" in styles
        )

        with app.state.database.session_factory() as session:
            proposal = (
                session.query(CatalogImportProposal).filter_by(batch_id=batch.id, position=26).one()
            )
            proposal_id = proposal.id
        saved = client.post(
            f"/admin/catalog-import/{batch.id}/review",
            data={
                "csrf_token": csrf(review),
                "page": "2",
                "proposal_id": str(proposal_id),
                f"name_{proposal_id}": "Edited Review Bourbon",
                f"size_{proposal_id}": "1 L",
                f"msrp_{proposal_id}": "55.50",
                f"price_updated_at_{proposal_id}": "2026-07-21",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert saved.headers["location"] == f"/admin/catalog-import/{batch.id}?page=2&saved=1"
        with app.state.database.session_factory() as session:
            edited = session.get(CatalogImportProposal, proposal_id)
            assert edited is not None
            assert (
                edited.name,
                edited.product_key,
                edited.size_key,
                edited.msrp,
                edited.included,
            ) == (
                "Edited Review Bourbon",
                "edited review bourbon",
                "1 l",
                55.5,
                False,
            )
            assert session.query(CatalogPrice).count() == 0


def test_catalog_import_review_page_navigation_renders_the_requested_rows(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id, proposal_count=51)

        first_page = client.get(f"/admin/catalog-import/{batch.id}?page=1")
        second_page = client.get(f"/admin/catalog-import/{batch.id}?page=2")
        third_page = client.get(f"/admin/catalog-import/{batch.id}?page=3")

        assert "Review Bourbon 1" in first_page.text
        assert "Review Bourbon 26" not in first_page.text
        assert "Review Bourbon 26" in second_page.text
        assert "Review Bourbon 1" not in second_page.text
        assert "Review Bourbon 25" not in second_page.text
        assert "Review Bourbon 50" in second_page.text
        assert "Review Bourbon 51" in third_page.text
        assert "Review Bourbon 50" not in third_page.text
        assert f'href="/admin/catalog-import/{batch.id}?page=2"' in first_page.text
        assert f'href="/admin/catalog-import/{batch.id}?page=3"' in second_page.text


def test_admin_catalog_import_review_rejects_invalid_or_nonreview_edits(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        review = client.get(f"/admin/catalog-import/{batch.id}")
        with app.state.database.session_factory() as session:
            proposal = session.query(CatalogImportProposal).filter_by(batch_id=batch.id).one()
            proposal_id = proposal.id
        invalid = client.post(
            f"/admin/catalog-import/{batch.id}/review",
            data={
                "csrf_token": csrf(review),
                "proposal_id": str(proposal_id),
                f"name_{proposal_id}": "",
                f"size_{proposal_id}": "750ml",
                f"msrp_{proposal_id}": "0",
                f"price_updated_at_{proposal_id}": "not-a-date",
            },
        )
        assert invalid.status_code == 400
        assert "Fix the required fields" in invalid.text
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportProposal, proposal_id)
            assert persisted is not None
            assert persisted.validation_error is not None
            persisted_batch = session.get(CatalogImportBatch, batch.id)
            assert persisted_batch is not None
            persisted_batch.state = "failed"
            session.commit()

        locked_page = client.get(f"/admin/catalog-import/{batch.id}")
        locked = client.post(
            f"/admin/catalog-import/{batch.id}/review",
            data={"csrf_token": csrf(locked_page), "proposal_id": str(proposal_id)},
        )
        assert locked.status_code == 409
        assert "Only batches awaiting review can be edited" in locked.text


def test_catalog_import_review_shows_current_price_context_and_apply_is_atomic(
    tmp_path: Path,
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id, proposal_count=2)
        prior_checked_at = datetime(2026, 7, 1, tzinfo=UTC)
        with app.state.database.session_factory() as session:
            session.add(
                CatalogPrice(
                    product_key="review bourbon 1",
                    size_key="750ml",
                    msrp=25.0,
                    title="Prior catalog",
                    url="https://example.test/prior",
                    checked_at=prior_checked_at,
                )
            )
            session.commit()

        review = client.get(f"/admin/catalog-import/{batch.id}")
        assert 'id="current-price-' in review.text
        assert 'value="25.00" readonly' in review.text
        assert 'value="2026-07-01" readonly' in review.text
        assert "No current price" in review.text
        assert "Apply included proposals to catalog" in review.text

        indexed_ids: list[int] = []

        class CommittedIndex:
            enabled = True

            async def upsert(self, price: CatalogPrice) -> bool:
                # The route must not hand any data to Qdrant until the import transaction has
                # committed its price rows and terminal batch state.
                with app.state.database.session_factory() as session:
                    persisted_batch = session.get(CatalogImportBatch, batch.id)
                    assert persisted_batch is not None and persisted_batch.state == "applied"
                    assert session.get(CatalogPrice, price.id) is not None
                indexed_ids.append(price.id)
                return True

        app.state.qdrant_price_index = CommittedIndex()
        applied = client.post(
            f"/admin/catalog-import/{batch.id}/apply",
            data={"csrf_token": csrf(review)},
            follow_redirects=False,
        )
        assert applied.status_code == 303
        assert "created=1" in applied.headers["location"]
        assert "updated=1" in applied.headers["location"]
        assert "skipped=0" in applied.headers["location"]
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None and persisted.state == "applied"
            existing = session.query(CatalogPrice).filter_by(product_key="review bourbon 1").one()
            created = session.query(CatalogPrice).filter_by(product_key="review bourbon 2").one()
            assert existing.msrp == 1.0
            assert existing.checked_at.date().isoformat() == "2026-07-22"
            assert created.msrp == 2.0
            assert created.title == "Local screenshot catalog"
            assert created.url == ""
            assert indexed_ids == [existing.id, created.id]


def test_catalog_import_apply_keeps_sql_committed_when_qdrant_is_disabled_or_fails(
    tmp_path: Path,
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")

        class DisabledIndex:
            enabled = False

            async def upsert(self, _price: CatalogPrice) -> bool:
                raise AssertionError("disabled indexes must not receive writes")

        app.state.qdrant_price_index = DisabledIndex()
        disabled_batch = create_review_batch(app, admin_id)
        disabled_page = client.get(f"/admin/catalog-import/{disabled_batch.id}")
        disabled_result = client.post(
            f"/admin/catalog-import/{disabled_batch.id}/apply",
            data={"csrf_token": csrf(disabled_page)},
            follow_redirects=False,
        )
        assert disabled_result.status_code == 303

        class FailingIndex:
            enabled = True

            async def upsert(self, _price: CatalogPrice) -> bool:
                raise RuntimeError("injected Qdrant outage")

        app.state.qdrant_price_index = FailingIndex()
        failing_batch = create_review_batch(app, admin_id)
        failing_page = client.get(f"/admin/catalog-import/{failing_batch.id}")
        failed_sync_result = client.post(
            f"/admin/catalog-import/{failing_batch.id}/apply",
            data={"csrf_token": csrf(failing_page)},
            follow_redirects=False,
        )
        assert failed_sync_result.status_code == 303
        with app.state.database.session_factory() as session:
            assert session.get(CatalogImportBatch, disabled_batch.id).state == "applied"
            assert session.get(CatalogImportBatch, failing_batch.id).state == "applied"
            assert (
                session.query(CatalogPrice)
                .filter_by(product_key="review bourbon 1", size_key="750ml")
                .count()
                == 1
            )


def test_catalog_import_apply_exclusion_rollback_and_state_rejection(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id, proposal_count=2)
        with app.state.database.session_factory() as session:
            old_price = CatalogPrice(
                product_key="review bourbon 1", size_key="750ml", msrp=30.0, url=""
            )
            session.add(old_price)
            proposal = (
                session.query(CatalogImportProposal).filter_by(batch_id=batch.id, position=1).one()
            )
            proposal.included = False
            session.commit()
        with app.state.database.session_factory() as session:
            result = apply_catalog_import_batch(session, batch.id)
            assert (result.created, result.updated, result.skipped) == (1, 0, 1)
        with app.state.database.session_factory() as session:
            retained = session.query(CatalogPrice).filter_by(product_key="review bourbon 1").one()
            assert retained.msrp == 30.0
            assert session.get(CatalogImportBatch, batch.id).state == "applied"
        with (
            app.state.database.session_factory() as session,
            pytest.raises(CatalogImportApplyStateError),
        ):
            apply_catalog_import_batch(session, batch.id)

        rollback_batch = create_review_batch(app, admin_id, proposal_count=2)
        with app.state.database.session_factory() as session:

            def fail_on_second(proposal: CatalogImportProposal) -> None:
                if proposal.position == 2:
                    raise RuntimeError("injected write failure")

            with pytest.raises(RuntimeError, match="injected write failure"):
                apply_catalog_import_batch(
                    session, rollback_batch.id, before_persist=fail_on_second
                )
        with app.state.database.session_factory() as session:
            assert session.get(CatalogImportBatch, rollback_batch.id).state == "review"
            assert (
                session.query(CatalogPrice)
                .filter(CatalogPrice.product_key.in_(["review bourbon 1", "review bourbon 2"]))
                .count()
                == 2
            )


def test_catalog_import_apply_preserves_newer_catalog_price(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        with app.state.database.session_factory() as session:
            proposal = session.query(CatalogImportProposal).filter_by(batch_id=batch.id).one()
            proposal.price_updated_at = datetime(2025, 1, 1, tzinfo=UTC).date()
            session.add(
                CatalogPrice(
                    product_key=proposal.product_key,
                    size_key=proposal.size_key,
                    msrp=99.99,
                    url="",
                    checked_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            session.commit()
        with app.state.database.session_factory() as session:
            result = apply_catalog_import_batch(session, batch.id)
            assert (result.created, result.updated, result.skipped) == (0, 0, 1)
        with app.state.database.session_factory() as session:
            price = session.query(CatalogPrice).filter_by(product_key="review bourbon 1").one()
            assert price.msrp == 99.99


def test_catalog_import_apply_requires_csrf_admin_and_review_state(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        assert client.post(f"/admin/catalog-import/{batch.id}/apply").status_code == 403

        page = client.get(f"/admin/catalog-import/{batch.id}")
        with app.state.database.session_factory() as session:
            session.get(CatalogImportBatch, batch.id).state = "failed"
            session.commit()
        denied = client.post(
            f"/admin/catalog-import/{batch.id}/apply", data={"csrf_token": csrf(page)}
        )
        assert denied.status_code == 409
        assert "Only batches awaiting review can be applied" in denied.text


def test_admin_deletes_only_a_safe_catalog_import_batch_and_its_sources(
    tmp_path: Path, monkeypatch
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        target = create_review_batch(app, admin_id)
        with app.state.database.session_factory() as session:
            persisted_target = session.get(CatalogImportBatch, target.id)
            assert persisted_target is not None
            persisted_target.state = "queued"
            duplicate = CatalogImportBatch(
                created_by_user_id=admin_id,
                state="queued",
                source_file_count=1,
            )
            session.add(duplicate)
            session.commit()
            duplicate_id = duplicate.id
        for batch_id in (target.id, duplicate_id):
            source = catalog_import_batch_directory(app.state.settings, batch_id)
            source.mkdir(parents=True)
            (source / "catalog.png").write_bytes(b"staged source")

        import bourbonbook.main as main

        async def provider_call(*_args) -> None:
            raise AssertionError("deleting a batch must not call a provider")

        async def qdrant_write(*_args) -> bool:
            raise AssertionError("deleting a batch must not write Qdrant")

        monkeypatch.setattr(main, "analyze_bottle", provider_call)
        monkeypatch.setattr(main, "analyze_bottle_name", provider_call)
        monkeypatch.setattr(app.state.qdrant_price_index, "upsert", qdrant_write)
        page = client.get(f"/admin/catalog-import/{target.id}")
        assert 'aria-label="Delete catalog import batch' in page.text
        assert "Delete this batch" in page.text

        deleted = client.post(
            f"/admin/catalog-import/{target.id}/delete",
            data={"csrf_token": csrf(page)},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert deleted.headers["location"] == "/admin/catalog-import?deleted=1"
        assert not catalog_import_batch_directory(app.state.settings, target.id).exists()
        assert catalog_import_batch_directory(app.state.settings, duplicate_id).exists()
        with app.state.database.session_factory() as session:
            assert session.get(CatalogImportBatch, target.id) is None
            assert session.query(CatalogImportProposal).filter_by(batch_id=target.id).count() == 0
            assert session.get(CatalogImportBatch, duplicate_id) is not None
            assert session.query(CatalogPrice).count() == 0


def test_admin_catalog_import_delete_requires_csrf_and_verified_admin(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        assert client.post(f"/admin/catalog-import/{batch.id}/delete").status_code == 403

        admin_profile = client.get("/profile")
        logout = client.post("/logout", data={"csrf_token": csrf(admin_profile)})
        assert logout.status_code == 200
        register(client, "member")
        profile = client.get("/profile")
        denied = client.post(
            f"/admin/catalog-import/{batch.id}/delete",
            data={"csrf_token": csrf(profile)},
        )
        assert denied.status_code == 403
        with app.state.database.session_factory() as session:
            assert session.get(CatalogImportBatch, batch.id) is not None


def test_admin_retries_failed_catalog_import_with_retained_sources(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None
            persisted.state = "failed"
            persisted.attempt_count = 2
            persisted.lease_expires_at = datetime(2026, 7, 22, tzinfo=UTC)
            persisted.error_summary = "transport"
            session.commit()
        source = catalog_import_batch_directory(app.state.settings, batch.id)
        source.mkdir(parents=True)
        (source / "catalog.png").write_bytes(b"staged source")

        page = client.get(f"/admin/catalog-import/{batch.id}")
        assert "Retry extraction" in page.text
        assert client.post(f"/admin/catalog-import/{batch.id}/retry").status_code == 403
        retried = client.post(
            f"/admin/catalog-import/{batch.id}/retry",
            data={"csrf_token": csrf(page)},
            follow_redirects=False,
        )
        assert retried.status_code == 303
        assert retried.headers["location"] == f"/admin/catalog-import/{batch.id}?retried=1"
        assert source.exists()
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None
            assert (
                persisted.state,
                persisted.attempt_count,
                persisted.error_summary,
                persisted.lease_expires_at,
            ) == ("queued", 0, None, None)

        duplicate_retry = client.post(
            f"/admin/catalog-import/{batch.id}/retry", data={"csrf_token": csrf(page)}
        )
        assert duplicate_retry.status_code == 409
        assert "Only failed catalog import batches can be retried" in duplicate_retry.text


def test_admin_retry_reports_missing_failed_source_without_changing_batch(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None
            persisted.state = "failed"
            persisted.error_summary = "filenotfounderror"
            session.commit()

        page = client.get(f"/admin/catalog-import/{batch.id}")
        unavailable = client.post(
            f"/admin/catalog-import/{batch.id}/retry", data={"csrf_token": csrf(page)}
        )
        assert unavailable.status_code == 409
        assert "staged source files are no longer available" in unavailable.text
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None
            assert (persisted.state, persisted.error_summary) == ("failed", "filenotfounderror")


def test_admin_catalog_import_delete_blocks_extracting_batches(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None
            persisted.state = "extracting"
            session.commit()
        page = client.get(f"/admin/catalog-import/{batch.id}")
        assert "Delete this batch" not in page.text
        blocked = client.post(
            f"/admin/catalog-import/{batch.id}/delete",
            data={"csrf_token": csrf(page)},
        )
        assert blocked.status_code == 409
        assert "in-process or completed catalog import cannot be deleted" in blocked.text
        with app.state.database.session_factory() as session:
            assert session.get(CatalogImportBatch, batch.id) is not None


def test_admin_catalog_import_delete_control_visibility_matches_batch_state(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        for state, expected in (
            ("queued", True),
            ("failed", True),
            ("review", True),
            ("extracting", False),
            ("applied", False),
            ("expired", False),
        ):
            with app.state.database.session_factory() as session:
                persisted = session.get(CatalogImportBatch, batch.id)
                assert persisted is not None
                persisted.state = state
                session.commit()
            page = client.get(f"/admin/catalog-import/{batch.id}")
            assert ("Delete this batch" in page.text) is expected


def test_admin_catalog_import_delete_rejects_applied_and_expired_batches(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        for state in ("applied", "expired"):
            with app.state.database.session_factory() as session:
                persisted = session.get(CatalogImportBatch, batch.id)
                assert persisted is not None
                persisted.state = state
                session.commit()
            page = client.get(f"/admin/catalog-import/{batch.id}")
            blocked = client.post(
                f"/admin/catalog-import/{batch.id}/delete",
                data={"csrf_token": csrf(page)},
            )
            assert blocked.status_code == 409
            with app.state.database.session_factory() as session:
                assert session.get(CatalogImportBatch, batch.id) is not None


def test_catalog_import_delete_is_safe_against_a_claim_from_another_session(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None
            persisted.state = "queued"
            session.commit()

        with app.state.database.session_factory() as claim_session:
            claimed = claim_next_catalog_import(
                claim_session, app.state.settings, datetime.now(UTC)
            )
            claim_session.commit()
        assert claimed is not None and claimed.id == batch.id
        with app.state.database.session_factory() as delete_session:
            assert not delete_catalog_import_batch(delete_session, batch.id)
            delete_session.commit()
        with app.state.database.session_factory() as session:
            persisted = session.get(CatalogImportBatch, batch.id)
            assert persisted is not None and persisted.state == "extracting"


def test_admin_catalog_import_delete_succeeds_when_staged_source_is_already_missing(
    tmp_path: Path,
) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        admin_id = promote_admin(app, "admin@example.com")
        batch = create_review_batch(app, admin_id)
        page = client.get(f"/admin/catalog-import/{batch.id}")
        deleted = client.post(
            f"/admin/catalog-import/{batch.id}/delete",
            data={"csrf_token": csrf(page)},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert not catalog_import_batch_directory(app.state.settings, batch.id).exists()


def test_admin_account_menu_lists_all_admin_screens_in_order(tmp_path: Path) -> None:
    client, app = make_client(tmp_path)
    with client:
        register(client, "admin")
        promote_admin(app, "admin@example.com")
        target_id = add_verified_user(app)

        page = client.get("/")

        profile = page.text.index('href="/profile"')
        divider = page.text.index('class="account-menu-divider"', profile)
        users = page.text.index('href="/admin/users"', divider)
        usage = page.text.index('href="/admin/usage"', users)
        config = page.text.index('href="/admin/config"', usage)
        signout = page.text.index('action="/logout"', config)
        assert profile < divider < users < usage < config < signout

        for path in (
            "/admin/users",
            "/admin/usage",
            "/admin/config",
            f"/admin/users/{target_id}",
        ):
            admin_page = client.get(path)
            assert '<details class="brand-menu"' in admin_page.text
            assert '<details class="account-menu"' in admin_page.text
            assert 'class="editor-bar"' not in admin_page.text


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
