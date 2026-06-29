from __future__ import annotations

import secrets

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from bourbonbook.models import User

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def normalize_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address.") from exc


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Use a password with at least 10 characters.")


def current_user(request: Request, session: Session) -> User | None:
    user_id = request.session.get("user_id")
    version = request.session.get("session_version")
    user = session.get(User, user_id) if user_id else None
    if not user or version != user.session_version:
        if user_id:
            request.session.clear()
        return None
    return user


def require_user(request: Request, session: Session) -> User:
    user = current_user(request, session)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_verified_user(request: Request, session: Session) -> User:
    user = require_user(request, session)
    if not user.email_verified_at:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/check-email"}
        )
    return user


def require_admin(request: Request, session: Session) -> User:
    user = require_verified_user(request, session)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


def authenticate_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid form token")
