from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.platform.database import Record, install_tables


class Base(DeclarativeBase):
    pass


install_tables(Base.metadata)


class User(Record, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    roles: Mapped[list] = mapped_column(JSONB, default=lambda: ["user"])
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(Text)
    mfa_pending_secret: Mapped[str | None] = mapped_column(Text)
    recovery_hashes: Mapped[list] = mapped_column(JSONB, default=list)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    deletion_id: Mapped[str | None] = mapped_column(String(36))


class Session(Record, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    user_agent: Mapped[str] = mapped_column(String(300), default="")


class SecurityToken(Record, Base):
    __tablename__ = "security_tokens"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SecurityAudit(Record, Base):
    __tablename__ = "security_audit"
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    action_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class AccountDeletion(Record, Base):
    __tablename__ = "account_deletions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    completed_services: Mapped[list] = mapped_column(JSONB, default=list)
