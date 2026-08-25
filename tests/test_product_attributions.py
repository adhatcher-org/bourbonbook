from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from bourbonbook.analysis import GroundedAttributions, GroundedFieldResult
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import Bottle, ProductAttributionFact, User
from bourbonbook.product_attributions import (
    canonical_public_url,
    product_attribution_key,
    resolve_attributions,
    set_provenance,
)


def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=10,
        max_upload_mb=2,
    )


def bottle(session) -> Bottle:
    user = User(username="owner", display_name="Owner", password_hash="hash")
    result = Bottle(owner=user, name="Elijah Craig Barrel Proof Rye", brand="Elijah Craig")
    session.add(result)
    session.flush()
    return result


def grounded(*, mash: bool = True) -> GroundedAttributions:
    return GroundedAttributions(
        GroundedFieldResult(
            "resolved", "Heaven Hill", "Heaven Hill", "https://heavenhill.com/ec", "Producer page"
        ),
        GroundedFieldResult(
            "resolved",
            "75% corn, 13% rye, 12% malted barley",
            "Heaven Hill",
            "https://heavenhill.com/ec",
            "Mash bill",
        )
        if mash
        else GroundedFieldResult("no_evidence"),
    )


def test_cache_is_field_independent_and_fresh_hits_need_no_provider(tmp_path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    try:
        with database.session_factory() as session:
            item = bottle(session)
            changed = asyncio.run(
                resolve_attributions(session, item, configured, provider_result=grounded())
            )
            session.commit()
            assert changed == {"distilled_by", "mash_bill"}
            assert item.distilled_by == "Heaven Hill"
            assert item.mash_bill.startswith("75%")
        with database.session_factory() as session:
            item = session.get(Bottle, 1)
            assert item is not None
            changed = asyncio.run(
                resolve_attributions(session, item, configured, provider_result=None)
            )
            assert changed == set()
            assert session.query(ProductAttributionFact).count() == 2
    finally:
        database.engine.dispose()


def test_precedence_and_no_evidence_only_clear_provider_recall(tmp_path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    try:
        with database.session_factory() as session:
            item = bottle(session)
            item.distilled_by = "User choice"
            item.mash_bill = "Recall value"
            set_provenance(item, "distilled_by", "user_entered")
            set_provenance(item, "mash_bill", "provider_recall")
            changed = asyncio.run(
                resolve_attributions(
                    session, item, configured, provider_result=grounded(mash=False)
                )
            )
            session.commit()
            assert changed == {"mash_bill"}
            assert item.distilled_by == "User choice"
            assert item.mash_bill == ""
    finally:
        database.engine.dispose()


def test_expired_fact_refreshes_and_invalid_url_does_not_persist(tmp_path) -> None:
    configured = settings(tmp_path)
    bootstrap_database(configured)
    database = Database(configured)
    try:
        with database.session_factory() as session:
            item = bottle(session)
            key = product_attribution_key(item)
            assert key
            session.add(
                ProductAttributionFact(
                    product_key=key,
                    field="distilled_by",
                    value="Old",
                    outcome="resolved",
                    title="Old",
                    url="https://example.com",
                    basis="Old",
                    checked_at=datetime.now(UTC) - timedelta(days=366),
                )
            )
            session.commit()
            changed = asyncio.run(
                resolve_attributions(session, item, configured, provider_result=grounded())
            )
            session.commit()
            assert "distilled_by" in changed
            assert (
                session.query(ProductAttributionFact).filter_by(field="distilled_by").one().value
                == "Heaven Hill"
            )
            assert canonical_public_url("http://127.0.0.1/x") is None
            assert canonical_public_url("file:///etc/passwd") is None
    finally:
        database.engine.dispose()
