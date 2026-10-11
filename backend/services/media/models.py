from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.platform.database import Record, install_tables


class Base(DeclarativeBase):
    pass


install_tables(Base.metadata)


class File(Record, Base):
    __tablename__ = "files"
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    purpose: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(100))
    quarantine_key: Mapped[str] = mapped_column(String(200), unique=True)
    object_version: Mapped[str | None] = mapped_column(String(200))
    final_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    rejection_code: Mapped[str | None] = mapped_column(String(100))
    reservation_id: Mapped[str | None] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FileBinding(Record, Base):
    __tablename__ = "file_bindings"
    __table_args__ = (UniqueConstraint("file_id", "resource_type", "resource_id"),)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
