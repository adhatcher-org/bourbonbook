from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from bourbonbook.models import User, UserToken

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"
PURPOSES = {VERIFY_EMAIL, RESET_PASSWORD}


def token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def issue_token(
    session: Session,
    user: User,
    purpose: str,
    ttl: timedelta,
    *,
    requested_ip_hash: str | None = None,
) -> tuple[UserToken, str]:
    if purpose not in PURPOSES or not user.email:
        raise ValueError("A valid token purpose and user email are required")
    now = datetime.now(UTC)
    revoke_tokens(session, user.id, purpose)
    raw_token = secrets.token_urlsafe(32)
    token = UserToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=token_digest(raw_token),
        email_snapshot=user.email,
        expires_at=now + ttl,
        requested_ip_hash=requested_ip_hash,
    )
    session.add(token)
    session.flush()
    return token, raw_token


def find_valid_token(session: Session, raw_token: str, purpose: str) -> UserToken | None:
    token = session.scalar(
        select(UserToken).where(
            UserToken.token_hash == token_digest(raw_token), UserToken.purpose == purpose
        )
    )
    return token if token_is_valid(token, purpose) else None


def token_is_valid(token: UserToken | None, purpose: str) -> bool:
    if not token or token.purpose != purpose or token.used_at is not None:
        return False
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > datetime.now(UTC)


def consume_token(session: Session, token_id: int, purpose: str) -> UserToken | None:
    token = session.get(UserToken, token_id)
    if not token_is_valid(token, purpose):
        return None
    token.used_at = datetime.now(UTC)
    session.flush()
    return token


def revoke_tokens(session: Session, user_id: int, purpose: str | None = None) -> None:
    statement = select(UserToken).where(UserToken.user_id == user_id, UserToken.used_at.is_(None))
    if purpose:
        statement = statement.where(UserToken.purpose == purpose)
    now = datetime.now(UTC)
    for token in session.scalars(statement):
        token.used_at = now
    session.flush()
