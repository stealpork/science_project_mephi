from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
    has_more: bool


class RecordDTO(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime
    version: int


class ProjectViewer(BaseModel):
    is_owner: bool
    is_member: bool
    can_manage: bool
    application_status: str | None
    invitation_status: str | None
    can_apply: bool
    cannot_apply_reason: str | None


class ProjectDTO(RecordDTO):
    creator_id: str
    creator_name: str
    owner_id: str | None
    title: str
    summary: str
    description: str
    research_goal: str
    area_id: str | None
    university_id: str | None
    interest_ids: list[str]
    required_skill_ids: list[str]
    required_roles: list[str]
    preferred_specialty_ids: list[str]
    eligible_courses: list[int]
    min_completed_projects: int | None
    team_size: int
    deadline: datetime | None
    access_type: Literal["public_open", "public_invite", "private_invite"]
    status: Literal["draft", "recruiting", "active", "completed", "archived"]
    moderation_status: Literal["visible", "hidden"]
    results: list[dict]
    members_count: int
    free_slots: int
    is_favorited: bool | None
    viewer: ProjectViewer


class ProfileViewer(BaseModel):
    is_self: bool
    can_invite: bool


class ContactDTO(BaseModel):
    type: str
    value: str
    visibility: Literal["public", "authenticated", "private"]


class ProfileDTO(RecordDTO):
    user_id: str
    first_name: str
    last_name: str
    bio: str
    university_id: str | None
    faculty_id: str | None
    department_id: str | None
    specialty_id: str | None
    city_id: str | None
    course: int | None
    avatar_file_id: str | None
    skills: list[dict]
    interests: list[dict]
    experience: list[dict]
    contacts: list[ContactDTO]
    viewer: ProfileViewer
    is_favorited: bool | None
    missing_fields: list[str] = Field(default_factory=list)
    completeness: int | None = None


class InitializingProfile(BaseModel):
    status: Literal["initializing"]


class AuthorDTO(BaseModel):
    author_id: str
    user_id: str | None
    display_name: str
    affiliation: str
    position: int
    access_granted_at: datetime | None


class PaperViewer(BaseModel):
    can_edit: bool
    is_owner: bool
    can_manage_authors: bool


class PaperDTO(RecordDTO):
    creator_id: str
    owner_id: str | None
    title: str
    abstract: str
    area_id: str | None
    university_id: str | None
    keywords: list[str]
    year: int | None
    external_links: list[str]
    project_id: str | None
    file_id: str | None
    status: Literal["draft", "published", "archived"]
    moderation_status: Literal["visible", "hidden"]
    views_count: int
    saves_count: int
    authors: list[AuthorDTO]
    viewer: PaperViewer
    is_favorited: bool | None


class UploadCreated(BaseModel):
    upload_id: str
    file_id: str
    upload_method: Literal["PUT"]
    upload_url: str
    required_headers: dict[str, str]
    form_fields: dict[str, str]
    expires_at: datetime
    reservation_id: str | None
    reservation_expires_at: datetime | None


class UploadState(BaseModel):
    upload_id: str
    file_id: str
    status: Literal["created", "uploaded", "scanning", "ready", "rejected", "expired", "cancelled"]
    rejection_code: str | None
    expires_at: datetime
    filename: str
    content_type: str
    size_bytes: int
    reservation_id: str | None


class AccountDTO(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    global_roles: list[str]
    account_status: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"]
    expires_in: int
    refresh_expires_in: int
    account: AccountDTO


class MFARequired(BaseModel):
    requires_2fa: Literal[True]
    challenge_token: str
    expires_in: int
