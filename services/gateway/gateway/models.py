"""Database tables (SQLAlchemy 2 typed models). The schema is created by the
Alembic migrations in gateway/migrations; a test checks the two never drift."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, MetaData, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint/index names, so migrations can refer to them by name.
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'member')", name="role_valid"),
        CheckConstraint("status IN ('active', 'invited', 'disabled')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    password_hash: Mapped[str | None] = mapped_column(Text)        # argon2; NULL until the invite is accepted
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = _now()


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)      # shown in the UI, e.g. "urk_Ab3dE6gH"
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256 hex of the full key
    created_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UpstreamServer(Base):
    __tablename__ = "upstream_servers"
    __table_args__ = (
        CheckConstraint("auth_type IN ('none', 'bearer', 'header')", name="auth_type_valid"),
        CheckConstraint("status IN ('unknown', 'connected', 'unreachable', 'disabled')", name="status_valid"),
        CheckConstraint("auth_type <> 'header' OR header_name IS NOT NULL", name="header_name_required"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)   # tool prefix: <slug>__<tool>
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    header_name: Mapped[str | None] = mapped_column(String(100))
    credential_encrypted: Mapped[str | None] = mapped_column(Text)   # Fernet token; never returned by any API
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint("channel IN ('api', 'chat')", name="channel_valid"),
        CheckConstraint("outcome IN ('success', 'error', 'timeout', 'denied')", name="outcome_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # SET NULL: deleting a user must not delete the evidence of what they did.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    server_slug: Mapped[str | None] = mapped_column(String(32))     # NULL if the tool name had no valid prefix
    tool: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
