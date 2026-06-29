from __future__ import annotations

import argparse
import getpass

from sqlalchemy import func, select

from bourbonbook.auth import hash_password, normalize_email, validate_password
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import User
from bourbonbook.tokens import revoke_tokens


def recover() -> None:
    settings = Settings.from_env()
    database = Database(settings)
    try:
        with database.session_factory() as session:
            if session.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))) != 1:
                raise SystemExit("Recovery requires exactly one administrator")
            admin = session.scalar(select(User).where(User.is_admin.is_(True)))
            email_text = input(f"Email [{admin.email or ''}]: ").strip()
            password = getpass.getpass("New password (leave blank to keep current): ")
            if email_text:
                email = normalize_email(email_text)
                duplicate = session.scalar(
                    select(User).where(User.email == email, User.id != admin.id)
                )
                if duplicate:
                    raise SystemExit("That email is already in use")
                admin.email = email
                admin.username = email
                admin.email_verified_at = None
            if password:
                validate_password(password)
                admin.password_hash = hash_password(password)
            if not email_text and not password:
                raise SystemExit("No changes requested")
            admin.session_version += 1
            revoke_tokens(session, admin.id)
            session.commit()
            print("Administrator recovered. Send a new verification link if the email changed.")
    finally:
        database.engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["recover"])
    args = parser.parse_args()
    if args.command == "recover":
        recover()


if __name__ == "__main__":
    main()
