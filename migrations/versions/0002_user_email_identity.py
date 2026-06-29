"""Add email identity, roles, session versions, and one-time tokens.

Revision ID: 0002_user_email_identity
Revises: 0001_current_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from email_validator import EmailNotValidError, validate_email

revision: str = "0002_user_email_identity"
down_revision: str | Sequence[str] | None = "0001_current_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalized_email(value: str) -> str | None:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column(
        "users",
        sa.Column("screen_name", sa.String(length=80), server_default="", nullable=False),
    )
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column(
        "users", sa.Column("session_version", sa.Integer(), server_default="1", nullable=False)
    )

    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT id, username, display_name FROM users")).mappings()
    normalized: dict[str, list[int]] = {}
    values: list[tuple[int, str | None, str]] = []
    for user in users:
        email = _normalized_email(user["username"])
        if email:
            normalized.setdefault(email, []).append(user["id"])
        values.append((user["id"], email, user["display_name"]))
    collisions = {email: ids for email, ids in normalized.items() if len(ids) > 1}
    if collisions:
        report = "; ".join(f"{email}: user ids {ids}" for email, ids in collisions.items())
        raise RuntimeError(f"Case-insensitive email collisions must be resolved: {report}")
    for user_id, email, screen_name in values:
        connection.execute(
            sa.text("UPDATE users SET email=:email, screen_name=:screen_name WHERE id=:id"),
            {"email": email, "screen_name": screen_name, "id": user_id},
        )

    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email_snapshot", sa.String(length=254), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_ip_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_tokens_user_id", "user_tokens", ["user_id"])
    op.create_index("ix_user_tokens_purpose", "user_tokens", ["purpose"])
    op.create_index("ix_user_tokens_token_hash", "user_tokens", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("user_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_email")
        batch.drop_column("session_version")
        batch.drop_column("is_admin")
        batch.drop_column("email_verified_at")
        batch.drop_column("screen_name")
        batch.drop_column("email")
