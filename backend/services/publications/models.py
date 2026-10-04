from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.platform.database import Record, install_tables


class Base(DeclarativeBase):
    pass


install_tables(Base.metadata)


class Paper(Record, Base):
    __tablename__ = "research_papers"
    creator_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(300))
    abstract: Mapped[str] = mapped_column(Text, default="")
    area_id: Mapped[str | None] = mapped_column(String(36), index=True)
    university_id: Mapped[str | None] = mapped_column(String(36), index=True)
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    external_links: Mapped[list] = mapped_column(JSONB, default=list)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    file_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    moderation_status: Mapped[str] = mapped_column(String(20), default="visible")
    views_count: Mapped[int] = mapped_column(Integer, default=0)
    saves_count: Mapped[int] = mapped_column(Integer, default=0)


class Author(Record, Base):
    __tablename__ = "paper_authors"
    __table_args__ = (
        Index(
            "one_account_per_paper",
            "paper_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )
    paper_id: Mapped[str] = mapped_column(ForeignKey("research_papers.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    affiliation: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[int] = mapped_column(Integer, default=1)
    access_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperAudit(Record, Base):
    __tablename__ = "paper_audit"
    actor_id: Mapped[str] = mapped_column(String(36))
    paper_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(100))
    action_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
