from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from libs.platform.database import Record, install_tables


class Base(DeclarativeBase):
    pass


install_tables(Base.metadata)


class Project(Record, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("team_size >= 1 AND team_size <= 100", name="valid_team_size"),
    )
    creator_id: Mapped[str] = mapped_column(String(36), index=True)
    creator_name: Mapped[str] = mapped_column(String(200), default="Исследователь")
    owner_id: Mapped[str | None] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(String(700), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    research_goal: Mapped[str] = mapped_column(Text, default="")
    area_id: Mapped[str | None] = mapped_column(String(36), index=True)
    university_id: Mapped[str | None] = mapped_column(String(36), index=True)
    interest_ids: Mapped[list] = mapped_column(JSONB, default=list)
    required_skill_ids: Mapped[list] = mapped_column(JSONB, default=list)
    required_roles: Mapped[list] = mapped_column(JSONB, default=list)
    preferred_specialty_ids: Mapped[list] = mapped_column(JSONB, default=list)
    eligible_courses: Mapped[list] = mapped_column(JSONB, default=list)
    min_completed_projects: Mapped[int | None] = mapped_column(Integer)
    team_size: Mapped[int] = mapped_column(Integer, default=5)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_type: Mapped[str] = mapped_column(String(30), default="public_open", index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    moderation_status: Mapped[str] = mapped_column(String(20), default="visible", index=True)
    results: Mapped[list] = mapped_column(JSONB, default=list)


class Member(Record, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(30))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Application(Record, Base):
    __tablename__ = "project_applications"
    __table_args__ = (
        Index(
            "one_pending_application",
            "project_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    message: Mapped[str] = mapped_column(Text)
    requested_role: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class Invitation(Record, Base):
    __tablename__ = "project_invitations"
    __table_args__ = (
        Index(
            "one_pending_invitation",
            "project_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    inviter_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectFile(Record, Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "file_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    file_id: Mapped[str] = mapped_column(String(36))
    uploaded_by: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200), default="")
    origin: Mapped[str] = mapped_column(String(20), default="attachment")
    paper_id: Mapped[str | None] = mapped_column(String(36))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FileReservation(Record, Base):
    __tablename__ = "project_file_reservations"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    file_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    replaces_file_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProjectSetting(Record, Base):
    __tablename__ = "project_settings"
    name: Mapped[str] = mapped_column(String(60), unique=True)
    value: Mapped[int] = mapped_column(Integer)


class ProjectAudit(Record, Base):
    __tablename__ = "project_audit"
    action_id: Mapped[str] = mapped_column(String(36), unique=True)
    actor_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text)
