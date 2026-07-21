from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bourbonbook.auth import hash_password, normalize_email, validate_password
from bourbonbook.config import Settings
from bourbonbook.email import EmailSender, link_message
from bourbonbook.models import User
from bourbonbook.tokens import RESET_PASSWORD, VERIFY_EMAIL, issue_token

logger = logging.getLogger(__name__)


async def issue_verification(
    session: Session, user: User, settings: Settings, sender: EmailSender
) -> str:
    _, raw = issue_token(
        session, user, VERIFY_EMAIL, timedelta(hours=settings.verification_ttl_hours)
    )
    session.commit()
    url = f"{settings.public_base_url}/verify-email?token={raw}"
    await sender.send(
        link_message(
            user.email or "", VERIFY_EMAIL, url, f"{settings.verification_ttl_hours} hours"
        )
    )
    return url


async def issue_reset(
    session: Session, user: User, settings: Settings, sender: EmailSender
) -> None:
    _, raw = issue_token(
        session, user, RESET_PASSWORD, timedelta(minutes=settings.reset_ttl_minutes)
    )
    session.commit()
    url = f"{settings.public_base_url}/reset-password?token={raw}"
    await sender.send(
        link_message(user.email or "", RESET_PASSWORD, url, f"{settings.reset_ttl_minutes} minutes")
    )


async def bootstrap_admin(session: Session, settings: Settings, sender: EmailSender) -> User | None:
    if session.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))):
        return None
    if not settings.default_admin_email or not settings.default_admin_password:
        if settings.app_env == "production":
            raise RuntimeError(
                "DEFAULT_ADMIN_EMAIL and DEFAULT_ADMIN_PASSWORD are required until an admin exists"
            )
        return None
    email = normalize_email(settings.default_admin_email)
    validate_password(settings.default_admin_password)
    user = session.scalar(select(User).where(User.email == email))
    if user:
        user.is_admin = True
    else:
        user = User(
            username=email,
            display_name="Administrator",
            email=email,
            screen_name="Administrator",
            password_hash=hash_password(settings.default_admin_password),
            is_admin=True,
        )
        session.add(user)
    session.flush()
    if settings.email_verification_required:
        await issue_verification(session, user, settings, sender)
    elif not user.email_verified_at:
        user.email_verified_at = datetime.now(UTC)
        session.commit()
    logger.warning(
        "Initial administrator created; remove DEFAULT_ADMIN_PASSWORD from configuration"
    )
    return user
