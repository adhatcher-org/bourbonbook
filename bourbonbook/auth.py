from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from bourbonbook.models import User

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def current_user(request: Request, session: Session) -> User | None:
    user_id = request.session.get("user_id")
    return session.get(User, user_id) if user_id else None


def require_user(request: Request, session: Session) -> User:
    user = current_user(request, session)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


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

