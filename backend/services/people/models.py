from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.platform.database import Record, install_tables


class Base(DeclarativeBase):
    pass


install_tables(Base.metadata)


class Profile(Record, Base):
    __tablename__ = "profiles"
    user_id: Mapped[str] = mapped_column(String(36), unique=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    bio: Mapped[str] = mapped_column(Text, default="")
    university_id: Mapped[str | None] = mapped_column(String(36), index=True)
    faculty_id: Mapped[str | None] = mapped_column(String(36), index=True)
    department_id: Mapped[str | None] = mapped_column(String(36))
    specialty_id: Mapped[str | None] = mapped_column(String(36), index=True)
    city_id: Mapped[str | None] = mapped_column(String(36), index=True)
    course: Mapped[int | None] = mapped_column(Integer)
    avatar_file_id: Mapped[str | None] = mapped_column(String(36))
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    interests: Mapped[list] = mapped_column(JSONB, default=list)
    experience: Mapped[list] = mapped_column(JSONB, default=list)
    contacts: Mapped[list] = mapped_column(JSONB, default=list)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class DictionaryEntry(Record, Base):
    __tablename__ = "dictionary_entries"
    __table_args__ = (UniqueConstraint("kind", "normalized_name", "parent_key"),)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(String(36))
    parent_key: Mapped[str] = mapped_column(String(36), default="")
    skill_kind: Mapped[str | None] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DictionarySuggestion(Record, Base):
    __tablename__ = "dictionary_suggestions"
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(String(36))
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    resolved_id: Mapped[str | None] = mapped_column(String(36))
    decision_reason: Mapped[str | None] = mapped_column(Text)
