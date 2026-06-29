from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import User, UserToken
from bourbonbook.tokens import (
    RESET_PASSWORD,
    VERIFY_EMAIL,
    consume_token,
    find_valid_token,
    issue_token,
    revoke_tokens,
)


def test_tokens_are_hashed_single_use_and_purpose_bound(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'tokens.db'}",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://invalid",
        ollama_model="test",
        max_users=10,
        max_upload_mb=2,
    )
    database = Database(settings)
    database.create_all()
    with database.session_factory() as session:
        user = User(
            username="person@example.com",
            display_name="Person",
            email="person@example.com",
            screen_name="Person",
            password_hash="hash",
        )
        session.add(user)
        session.flush()
        token, raw = issue_token(session, user, VERIFY_EMAIL, timedelta(hours=1))
        session.commit()
        assert raw not in token.token_hash
        assert raw not in session.scalar(select(UserToken.token_hash))
        assert find_valid_token(session, raw, RESET_PASSWORD) is None
        assert find_valid_token(session, raw, VERIFY_EMAIL).id == token.id
        assert consume_token(session, token.id, VERIFY_EMAIL) is not None
        session.commit()
        assert consume_token(session, token.id, VERIFY_EMAIL) is None

        newer, _ = issue_token(session, user, RESET_PASSWORD, timedelta(hours=1))
        revoke_tokens(session, user.id, RESET_PASSWORD)
        session.commit()
        assert consume_token(session, newer.id, RESET_PASSWORD) is None
    database.engine.dispose()
