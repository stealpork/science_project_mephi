from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import exists, func, or_, select, update

from libs.platform.api import Command, assert_version, command, create_app, dto, paginate
from libs.platform.database import append_event, now, session, uid
from libs.platform.errors import APIError, require
from libs.platform.http import (
    current_user,
    enrich_favorites,
    internal,
    internal_call,
    optional_user,
)
from libs.platform.schemas import Page, ProjectDTO
from services.projects.domain import ROLES, recommendation
from services.projects.models import (
    Application,
    Invitation,
    Member,
    Project,
)

app = create_app("projects")
Access = Literal["public_open", "public_invite", "private_invite"]


class ProjectInput(Command):
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(default="", max_length=700)
    description: str = Field(default="", max_length=20000)
    research_goal: str = Field(default="", max_length=10000)
    area_id: str | None = None
    university_id: str | None = None
    interest_ids: list[str] = Field(default_factory=list, max_length=30)
    required_skill_ids: list[str] = Field(default_factory=list, max_length=50)
    required_roles: list[str] = Field(default_factory=list, max_length=10)
    preferred_specialty_ids: list[str] = Field(default_factory=list, max_length=30)
    min_completed_projects: int | None = Field(default=None, ge=0, le=100)
    eligible_courses: list[int] = Field(default_factory=list, max_length=8)
    team_size: int = Field(default=5, ge=1, le=100)
    deadline: datetime | None = None
    access_type: Access = "public_open"


class ProjectPatch(Command):
    non_nullable_fields = frozenset(
        {
            "title",
            "summary",
            "description",
            "research_goal",
            "interest_ids",
            "required_skill_ids",
            "required_roles",
            "preferred_specialty_ids",
            "eligible_courses",
            "team_size",
            "access_type",
            "results",
        }
    )
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=3, max_length=200)
    summary: str | None = Field(default=None, max_length=700)
    description: str | None = Field(default=None, max_length=20000)
    research_goal: str | None = Field(default=None, max_length=10000)
    area_id: str | None = None
    university_id: str | None = None
    interest_ids: list[str] | None = Field(default=None, max_length=30)
    required_skill_ids: list[str] | None = Field(default=None, max_length=50)
    required_roles: list[str] | None = Field(default=None, max_length=10)
    preferred_specialty_ids: list[str] | None = Field(default=None, max_length=30)
    min_completed_projects: int | None = Field(default=None, ge=0, le=100)
    eligible_courses: list[int] | None = Field(default=None, max_length=8)
    team_size: int | None = Field(default=None, ge=1, le=100)
    deadline: datetime | None = None
    access_type: Access | None = None
    results: list[dict] | None = Field(default=None, max_length=30)
    confirm_visibility_expansion: bool = False


def visible_query(user: dict | None):
    public = (
        (Project.access_type != "private_invite")
        & (Project.status.not_in(["draft", "archived"]))
        & (Project.moderation_status == "visible")
    )
    if not user:
        return public
    membership = exists(
        select(Member.id).where(
            Member.project_id == Project.id, Member.user_id == user["id"], Member.left_at.is_(None)
        )
    )
    return or_(
        public,
        (Project.moderation_status == "visible") & or_(Project.owner_id == user["id"], membership),
    )


async def get_project(db, project_id: str, user: dict | None, lock=False) -> Project:
    query = select(Project).where(Project.id == project_id, visible_query(user))
    row = await db.scalar(query.with_for_update() if lock else query)
    require(row is not None, "NOT_FOUND", "Проект не найден", 404)
    return row


async def own_project(db, project_id: str, user: dict, lock=True) -> Project:
    row = await get_project(db, project_id, user, lock)
    require(row.owner_id == user["id"], "FORBIDDEN", "Требуются права руководителя", 403)
    return row


async def membership(db, project_id: str, user_id: str) -> Member | None:
    return await db.scalar(
        select(Member).where(
            Member.project_id == project_id, Member.user_id == user_id, Member.left_at.is_(None)
        )
    )


async def member_count(db, project_id: str) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Member)
            .where(Member.project_id == project_id, Member.left_at.is_(None))
        )
        or 0
    )


def verified(request, user):
    require(
        not request.app.state.settings.email_verification_required or user["email_verified"],
        "EMAIL_NOT_VERIFIED",
        "Подтвердите email",
        403,
    )


async def validate_fields(request, values: dict):
    ids = []
    for key in ("area_id", "university_id"):
        if values.get(key):
            ids.append(values[key])
    for key in ("interest_ids", "required_skill_ids", "preferred_specialty_ids"):
        ids.extend(values.get(key) or [])
    if ids:
        references = await internal_call(
            request,
            "people",
            "POST",
            "/internal/v1/dictionaries/validate",
            json={"ids": list(set(ids))},
        )
        kinds = {item["id"]: item["kind"] for item in references["items"]}
        for field, kind in {
            "area_id": "research_areas",
            "university_id": "universities",
            "interest_ids": "research_interests",
            "required_skill_ids": "skills",
            "preferred_specialty_ids": "specialties",
        }.items():
            selected = values.get(field) or []
            selected = selected if isinstance(selected, list) else [selected]
            require(
                all(kinds[value] == kind for value in selected),
                "INVALID_DICTIONARY_KIND",
                "Неверный тип значения справочника",
                422,
            )
    if values.get("required_roles"):
        require(
            set(values["required_roles"]) <= ROLES - {"leader"},
            "INVALID_ROLE",
            "Неизвестная роль",
            422,
        )
    if values.get("deadline"):
        require(
            values["deadline"].tzinfo is not None,
            "INVALID_DEADLINE",
            "Укажите часовой пояс дедлайна",
            422,
        )


async def project_view(db, row: Project, user: dict | None, facts=None) -> dict:
    result = dto(row)
    count = facts["counts"].get(row.id, 0) if facts is not None else await member_count(db, row.id)
    member = (
        facts["members"].get(row.id)
        if facts is not None
        else await membership(db, row.id, user["id"])
        if user
        else None
    )
    application = (
        facts["applications"].get(row.id)
        if facts is not None
        else await db.scalar(
            select(Application).where(
                Application.project_id == row.id,
                Application.user_id == user["id"],
                Application.status == "pending",
            )
        )
        if user
        else None
    )
    invitation = (
        facts["invitations"].get(row.id)
        if facts is not None
        else await db.scalar(
            select(Invitation).where(
                Invitation.project_id == row.id,
                Invitation.user_id == user["id"],
                Invitation.status == "pending",
                Invitation.expires_at > now(),
            )
        )
        if user
        else None
    )
    reason = (
        "AUTH_REQUIRED"
        if not user
        else "ALREADY_MEMBER"
        if member
        else "OWNER_UNAVAILABLE"
        if not row.owner_id
        else "APPLICATION_PENDING"
        if application
        else "INVITE_ONLY"
        if row.access_type != "public_open"
        else "RECRUITMENT_CLOSED"
        if row.status != "recruiting"
        else "PROJECT_FULL"
        if count >= row.team_size
        else None
    )
    result.update(
        members_count=count,
        free_slots=max(0, row.team_size - count),
        is_favorited=None,
        viewer={
            "is_owner": bool(user and row.owner_id == user["id"]),
            "is_member": bool(member),
            "can_manage": bool(user and row.owner_id == user["id"] and row.status != "archived"),
            "application_status": application.status if application else None,
            "invitation_status": invitation.status if invitation else None,
            "can_apply": reason is None,
            "cannot_apply_reason": reason,
        },
    )
    return result


async def project_cards(db, rows, user):
    if not rows:
        return []
    ids = [row.id for row in rows]
    counts = dict(
        (
            await db.execute(
                select(Member.project_id, func.count())
                .where(Member.project_id.in_(ids), Member.left_at.is_(None))
                .group_by(Member.project_id)
            )
        ).all()
    )
    facts = {"counts": counts, "members": {}, "applications": {}, "invitations": {}}
    if user:
        queries: list[tuple[Any, str, list[Any]]] = [
            (Member, "members", [Member.left_at.is_(None)]),
            (Application, "applications", [Application.status == "pending"]),
            (
                Invitation,
                "invitations",
                [Invitation.status == "pending", Invitation.expires_at > now()],
            ),
        ]
        for model, key, conditions in queries:
            values = (
                await db.scalars(
                    select(model).where(
                        model.project_id.in_(ids), model.user_id == user["id"], *conditions
                    )
                )
            ).all()
            facts[key] = {value.project_id: value for value in values}
    return [await project_view(db, row, user, facts) for row in rows]


async def catalog_cards(db, items, user):
    ids = [item["id"] for item in items]
    rows = {
        row.id: row for row in (await db.scalars(select(Project).where(Project.id.in_(ids)))).all()
    }
    return await project_cards(db, [rows[identifier] for identifier in ids], user)


@app.post("/api/v1/projects", status_code=201, response_model=ProjectDTO)
async def create_project(
    body: ProjectInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    verified(request, user)

    async def operation():
        values = body.model_dump()
        await validate_fields(request, values)
        profile = await internal_call(
            request, "people", "GET", f"/internal/v1/profiles/{user['id']}"
        )
        name = " ".join(filter(None, [profile.get("first_name"), profile.get("last_name")]))
        row = Project(
            id=uid(),
            creator_id=user["id"],
            creator_name=name or "Исследователь",
            owner_id=user["id"],
            **values,
        )
        db.add(row)
        await db.flush()
        db.add(Member(project_id=row.id, user_id=user["id"], role="leader"))
        await db.flush()
        return await project_view(db, row, user)

    return await command(request, db, operation, 201)


@app.get("/api/v1/projects", response_model=Page[ProjectDTO])
async def projects(
    request: Request,
    q: str = "",
    limit: int = 20,
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    query = select(Project).where(visible_query(user))
    if q:
        query = query.where(or_(Project.title.ilike(f"%{q}%"), Project.description.ilike(f"%{q}%")))
    for key in ("area_id", "university_id", "status", "access_type", "owner_id"):
        values = request.query_params.getlist(key)
        if values:
            query = query.where(getattr(Project, key).in_(values))
    if request.query_params.get("member_id"):
        query = query.where(
            exists(
                select(Member.id).where(
                    Member.project_id == Project.id,
                    Member.user_id == request.query_params["member_id"],
                    Member.left_at.is_(None),
                )
            )
        )
    skills = request.query_params.getlist("skill_id")
    if skills:
        conditions = [Project.required_skill_ids.contains([v]) for v in skills]
        query = (
            query.where(*conditions)
            if request.query_params.get("skills_mode") == "all"
            else query.where(or_(*conditions))
        )
    for parameter, operator in [
        ("created_from", Project.created_at.__ge__),
        ("created_to", Project.created_at.__le__),
    ]:
        if request.query_params.get(parameter):
            try:
                value = datetime.fromisoformat(request.query_params[parameter])
                if value.tzinfo is None:
                    raise ValueError("Timezone required")
                query = query.where(operator(value))
            except ValueError:
                raise APIError(422, "INVALID_DATE", "Укажите дату с часовым поясом") from None
    for param, operator in (
        ("team_size_min", Project.team_size.__ge__),
        ("team_size_max", Project.team_size.__le__),
    ):
        if request.query_params.get(param):
            try:
                query = query.where(operator(int(request.query_params[param])))
            except ValueError:
                raise APIError(422, "INVALID_FILTER", "Некорректный размер команды") from None
    if request.query_params.get("has_free_slots") == "true":
        count = (
            select(func.count())
            .select_from(Member)
            .where(Member.project_id == Project.id, Member.left_at.is_(None))
            .scalar_subquery()
        )
        query = query.where(count < Project.team_size, Project.status == "recruiting")
    result = await paginate(db, query, Project, request, limit)
    result["items"] = await enrich_favorites(
        request, "project", user, await catalog_cards(db, result["items"], user)
    )
    return result


@app.get("/api/v1/projects/{project_id}", response_model=ProjectDTO)
async def project_detail(
    project_id: str,
    request: Request,
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    cards = [await project_view(db, await get_project(db, project_id, user), user)]
    return (await enrich_favorites(request, "project", user, cards))[0]


@app.patch("/api/v1/projects/{project_id}", response_model=ProjectDTO)
async def edit_project(
    project_id: str,
    body: ProjectPatch,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    row = await own_project(db, project_id, user)
    require(
        row.status != "archived", "READ_ONLY", "Архивный проект доступен только для чтения", 403
    )
    assert_version(row, body.version)
    values = body.model_dump(
        exclude_unset=True, exclude={"version", "confirm_visibility_expansion"}
    )
    require(
        not (
            row.access_type == "private_invite"
            and values.get("access_type", row.access_type) != "private_invite"
        )
        or body.confirm_visibility_expansion,
        "VISIBILITY_CONFIRMATION_REQUIRED",
        "Подтвердите раскрытие проекта",
    )
    if values.get("team_size"):
        require(
            values["team_size"] >= await member_count(db, row.id),
            "TEAM_TOO_SMALL",
            "Размер меньше числа участников",
        )
    await validate_fields(request, values)
    for field, value in values.items():
        setattr(row, field, value)
    await append_event(
        db, "projects.project_updated", row.id, {"project_id": row.id, "version": row.version}
    )
    return await project_view(db, row, user)


async def transition(project_id, target, request, user, db):
    async def operation():
        row = await own_project(db, project_id, user)
        allowed = {
            "recruiting": {"draft", "active"},
            "active": {"recruiting"},
            "completed": {"active"},
            "archived": {"draft", "recruiting", "active", "completed"},
        }
        require(row.status in allowed[target], "INVALID_STATE", "Недопустимый переход статуса")
        if target == "recruiting":
            require(
                bool(row.description and row.research_goal and row.area_id),
                "PROJECT_INCOMPLETE",
                "Заполните описание, цель и направление",
                422,
            )
            require(
                await member_count(db, row.id) < row.team_size, "PROJECT_FULL", "В команде нет мест"
            )
        row.status, row.version = target, row.version + 1
        if target != "recruiting":
            await db.execute(
                update(Application)
                .where(Application.project_id == row.id, Application.status == "pending")
                .values(status="cancelled", decided_at=now())
            )
            await db.execute(
                update(Invitation)
                .where(Invitation.project_id == row.id, Invitation.status == "pending")
                .values(status="revoked")
            )
        members = (
            await db.scalars(
                select(Member.user_id).where(Member.project_id == row.id, Member.left_at.is_(None))
            )
        ).all()
        await append_event(
            db,
            "projects.project_updated",
            row.id,
            {
                "project_id": row.id,
                "status": target,
                "member_ids": list(members),
                "title": row.title,
                "access_type": row.access_type,
            },
        )
        return await project_view(db, row, user)

    return await command(request, db, operation)


@app.post("/api/v1/projects/{project_id}/publish")
async def publish(
    project_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    return await transition(project_id, "recruiting", request, user, db)


@app.post("/api/v1/projects/{project_id}/start")
async def start(
    project_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    return await transition(project_id, "active", request, user, db)


@app.post("/api/v1/projects/{project_id}/reopen")
async def reopen(
    project_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    return await transition(project_id, "recruiting", request, user, db)


@app.post("/api/v1/projects/{project_id}/complete")
async def complete(
    project_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    return await transition(project_id, "completed", request, user, db)


@app.delete("/api/v1/projects/{project_id}")
async def archive(
    project_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    return await transition(project_id, "archived", request, user, db)


@app.get("/api/v1/me/projects")
@app.get("/api/v1/me/teams")
async def own_projects(
    request: Request,
    ownership: str = "any",
    status: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    member = exists(
        select(Member.id).where(
            Member.project_id == Project.id, Member.user_id == user["id"], Member.left_at.is_(None)
        )
    )
    query = select(Project).where(
        Project.owner_id == user["id"]
        if ownership == "owner"
        else member
        if ownership == "member"
        else or_(Project.owner_id == user["id"], member)
    )
    if status:
        query = query.where(Project.status == status)
    result = await paginate(db, query, Project, request, limit)
    result["items"] = await enrich_favorites(
        request, "project", user, await catalog_cards(db, result["items"], user)
    )
    return result


class ApplicationInput(Command):
    message: str = Field(min_length=10, max_length=4000)
    requested_role: str = "researcher"


class DecisionInput(Command):
    role: str = "researcher"
    reason: str = Field(default="", max_length=2000)


async def add_member(db, project: Project, user_id: str, role: str):
    require(role in ROLES - {"leader"}, "INVALID_ROLE", "Недопустимая роль", 422)
    require(
        project.status == "recruiting" and project.owner_id is not None,
        "RECRUITMENT_CLOSED",
        "Набор закрыт",
    )
    require(
        await membership(db, project.id, user_id) is None,
        "ALREADY_MEMBER",
        "Пользователь уже в команде",
    )
    require(
        await member_count(db, project.id) < project.team_size,
        "PROJECT_FULL",
        "В команде нет свободных мест",
    )
    member = await db.scalar(
        select(Member).where(Member.project_id == project.id, Member.user_id == user_id)
    )
    if member:
        member.left_at, member.role, member.created_at = None, role, now()
    else:
        member = Member(id=uid(), project_id=project.id, user_id=user_id, role=role)
        db.add(member)
    await db.execute(
        update(Application)
        .where(
            Application.project_id == project.id,
            Application.user_id == user_id,
            Application.status == "pending",
        )
        .values(status="accepted", decided_at=now())
    )
    await db.execute(
        update(Invitation)
        .where(
            Invitation.project_id == project.id,
            Invitation.user_id == user_id,
            Invitation.status == "pending",
        )
        .values(status="accepted")
    )
    await append_event(
        db,
        "projects.member_joined",
        project.id,
        {"project_id": project.id, "user_id": user_id, "owner_id": project.owner_id},
    )
    await db.flush()
    return member


@app.post("/api/v1/projects/{project_id}/applications", status_code=201)
async def apply(
    project_id: str,
    body: ApplicationInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    verified(request, user)

    async def operation():
        project = await get_project(db, project_id, user, True)
        require(project.access_type == "public_open", "INVITE_ONLY", "Только по приглашению")
        require(
            project.status == "recruiting" and project.owner_id is not None,
            "RECRUITMENT_CLOSED",
            "Набор закрыт",
        )
        require(
            not await membership(db, project_id, user["id"]), "ALREADY_MEMBER", "Вы уже в команде"
        )
        require(
            await member_count(db, project_id) < project.team_size,
            "PROJECT_FULL",
            "В команде нет мест",
        )
        require(body.requested_role in ROLES - {"leader"}, "INVALID_ROLE", "Недопустимая роль", 422)
        previous = await db.scalar(
            select(Application)
            .where(Application.project_id == project_id, Application.user_id == user["id"])
            .order_by(Application.created_at.desc())
            .limit(1)
        )
        require(
            not previous or previous.status != "pending", "APPLICATION_PENDING", "Заявка уже подана"
        )
        require(
            not previous
            or previous.status != "rejected"
            or previous.decided_at < now() - timedelta(days=7),
            "APPLICATION_COOLDOWN",
            "Повторная заявка возможна через 7 дней",
        )
        row = Application(id=uid(), project_id=project_id, user_id=user["id"], **body.model_dump())
        db.add(row)
        await db.flush()
        await append_event(
            db,
            "projects.application_submitted",
            project_id,
            {
                "project_id": project_id,
                "application_id": row.id,
                "user_id": user["id"],
                "owner_id": project.owner_id,
            },
        )
        return dto(row)

    return await command(request, db, operation, 201)


@app.get("/api/v1/projects/{project_id}/applications")
async def list_applications(
    project_id: str,
    request: Request,
    status: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    await own_project(db, project_id, user, False)
    query = select(Application).where(Application.project_id == project_id)
    if status:
        query = query.where(Application.status == status)
    return await paginate(db, query, Application, request, limit)


@app.get("/api/v1/me/applications")
async def my_applications(
    request: Request,
    status: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    query = select(Application).where(Application.user_id == user["id"])
    if status:
        query = query.where(Application.status == status)
    return await paginate(db, query, Application, request, limit)


@app.post("/api/v1/projects/{project_id}/applications/{application_id}/{action}")
async def decide_application(
    project_id: str,
    application_id: str,
    action: Literal["accept", "reject", "withdraw"],
    request: Request,
    body: DecisionInput = DecisionInput(),
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        project = await get_project(db, project_id, user, True)
        row = await db.get(Application, application_id)
        require(
            row is not None and row.project_id == project_id, "NOT_FOUND", "Заявка не найдена", 404
        )
        require(
            (action == "withdraw" and row.user_id == user["id"])
            or (action != "withdraw" and project.owner_id == user["id"]),
            "FORBIDDEN",
            "Нет права на решение",
            403,
        )
        require(row.status == "pending", "INVALID_STATE", "Заявка уже рассмотрена")
        if action == "accept":
            candidate = await internal_call(
                request, "identity", "GET", f"/internal/v1/users/{row.user_id}"
            )
            require(candidate["status"] == "active", "ACCOUNT_BLOCKED", "Кандидат недоступен", 403)
            member = await add_member(db, project, row.user_id, body.role)
            await db.refresh(row)
            result = {"application": dto(row), "member": dto(member)}
        else:
            row.status, row.decided_at, row.decision_reason = (
                ("withdrawn" if action == "withdraw" else "rejected"),
                now(),
                body.reason,
            )
            result = {"application": dto(row)}
        await append_event(
            db,
            "projects.application_decided",
            project_id,
            {"project_id": project_id, "user_id": row.user_id, "status": row.status},
        )
        return result

    return await command(request, db, operation)


class InvitationInput(Command):
    user_id: str
    role: str = "researcher"
    message: str = Field(default="", max_length=2000)
    expires_at: datetime | None = None


@app.post("/api/v1/projects/{project_id}/invitations", status_code=201)
async def invite(
    project_id: str,
    body: InvitationInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        project = await own_project(db, project_id, user)
        require(project.status == "recruiting", "RECRUITMENT_CLOSED", "Набор закрыт")
        require(body.role in ROLES - {"leader"}, "INVALID_ROLE", "Недопустимая роль", 422)
        require(
            not await membership(db, project_id, body.user_id), "ALREADY_MEMBER", "Уже в команде"
        )
        candidate = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{body.user_id}"
        )
        require(candidate["status"] == "active", "ACCOUNT_BLOCKED", "Пользователь недоступен", 403)
        expiry = body.expires_at or now() + timedelta(days=7)
        require(
            expiry.tzinfo is not None and now() < expiry <= now() + timedelta(days=30),
            "INVALID_EXPIRY",
            "Срок от текущего времени до 30 дней",
            422,
        )
        await db.execute(
            update(Invitation)
            .where(
                Invitation.project_id == project_id,
                Invitation.user_id == body.user_id,
                Invitation.status == "pending",
                Invitation.expires_at <= now(),
            )
            .values(status="expired")
        )
        row = Invitation(
            id=uid(),
            project_id=project_id,
            user_id=body.user_id,
            inviter_id=user["id"],
            role=body.role,
            message=body.message,
            expires_at=expiry,
        )
        db.add(row)
        await db.flush()
        await append_event(
            db,
            "projects.invitation_created",
            project_id,
            {"project_id": project_id, "invitation_id": row.id, "user_id": body.user_id},
        )
        return dto(row)

    return await command(request, db, operation, 201)


@app.get("/api/v1/projects/{project_id}/invitations")
async def list_invitations(
    project_id: str,
    request: Request,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    await own_project(db, project_id, user, False)
    return await paginate(
        db,
        select(Invitation).where(Invitation.project_id == project_id),
        Invitation,
        request,
        limit,
    )


@app.get("/api/v1/me/invitations")
async def my_invitations(
    request: Request,
    status: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    query = select(Invitation).where(Invitation.user_id == user["id"])
    if status:
        query = query.where(Invitation.status == status)
    result = await paginate(db, query, Invitation, request, limit)
    for item in result["items"]:
        if (
            item["status"] == "pending"
            and datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00")) <= now()
        ):
            item["status"] = "expired"
        project = await db.get(Project, item["project_id"])
        item["project"] = (
            {
                "id": project.id,
                "title": project.title,
                "summary": project.summary,
                "owner_id": project.owner_id,
                "access_type": project.access_type,
            }
            if project
            and project.moderation_status == "visible"
            and (item["status"] == "pending" or await membership(db, project.id, user["id"]))
            else None
        )
    return result


@app.post("/api/v1/projects/{project_id}/invitations/{invitation_id}/{action}")
async def decide_invitation(
    project_id: str,
    invitation_id: str,
    action: Literal["accept", "decline", "revoke"],
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():

        project = await db.scalar(
            select(Project)
            .where(Project.id == project_id, Project.moderation_status == "visible")
            .with_for_update()
        )
        row = await db.get(Invitation, invitation_id)
        require(
            project is not None and row is not None and row.project_id == project_id,
            "NOT_FOUND",
            "Приглашение не найдено",
            404,
        )
        require(
            (action == "revoke" and project.owner_id == user["id"])
            or (action != "revoke" and row.user_id == user["id"]),
            "FORBIDDEN",
            "Нет прав",
            403,
        )
        require(row.status == "pending", "INVALID_STATE", "Приглашение уже рассмотрено")
        require(row.expires_at > now(), "INVITATION_EXPIRED", "Приглашение истекло")
        if action == "accept":
            await add_member(db, project, user["id"], row.role)
            await db.refresh(row)
        else:
            row.status = "declined" if action == "decline" else "revoked"
        await append_event(
            db,
            "projects.invitation_decided",
            project_id,
            {
                "project_id": project_id,
                "user_id": row.user_id,
                "owner_id": project.owner_id,
                "status": row.status,
            },
        )
        return dto(row)

    return await command(request, db, operation)


@app.get("/api/v1/projects/{project_id}/members")
async def members(
    project_id: str, user=Depends(optional_user), db=Depends(session, scope="function")
):
    await get_project(db, project_id, user)
    rows = (
        await db.scalars(
            select(Member)
            .where(Member.project_id == project_id, Member.left_at.is_(None))
            .order_by(Member.created_at, Member.id)
        )
    ).all()
    return {
        "items": [dict(dto(row), joined_at=row.created_at.isoformat()) for row in rows],
        "has_more": False,
        "next_cursor": None,
    }


class RoleInput(Command):
    role: str


@app.patch("/api/v1/projects/{project_id}/members/{user_id}")
async def edit_member(
    project_id: str,
    user_id: str,
    body: RoleInput,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    project = await own_project(db, project_id, user)
    member = await membership(db, project_id, user_id)
    require(
        member is not None and project.owner_id != user_id,
        "INVALID_MEMBER",
        "Участник не найден или является руководителем",
    )
    require(body.role in ROLES - {"leader"}, "INVALID_ROLE", "Недопустимая роль", 422)
    assert member is not None
    member.role = body.role
    return dto(member)


async def remove_member(db, project: Project, user_id: str):
    require(project.owner_id != user_id, "TRANSFER_REQUIRED", "Сначала передайте руководство")
    member = await membership(db, project.id, user_id)
    require(member is not None, "NOT_FOUND", "Участник не найден", 404)
    assert member is not None
    member.left_at = now()
    await append_event(
        db, "projects.member_left", project.id, {"project_id": project.id, "user_id": user_id}
    )


@app.delete("/api/v1/projects/{project_id}/members/{user_id}", status_code=204)
async def kick(
    project_id: str, user_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    await remove_member(db, await own_project(db, project_id, user), user_id)


@app.post("/api/v1/projects/{project_id}/leave", status_code=204)
async def leave(project_id: str, user=Depends(current_user), db=Depends(session, scope="function")):
    await remove_member(db, await get_project(db, project_id, user, True), user["id"])


class TransferInput(Command):
    user_id: str


@app.post("/api/v1/projects/{project_id}/transfer-ownership")
async def transfer(
    project_id: str,
    body: TransferInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        project = await own_project(db, project_id, user)
        target = await membership(db, project_id, body.user_id)
        require(
            target is not None and body.user_id != user["id"],
            "INVALID_MEMBER",
            "Выберите другого участника",
        )
        account = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{body.user_id}"
        )
        require(account["status"] == "active", "ACCOUNT_UNAVAILABLE", "Участник недоступен")
        old = await membership(db, project_id, user["id"])
        assert old is not None and target is not None
        old.role, target.role, project.owner_id = "researcher", "leader", body.user_id
        return await project_view(db, project, user)

    return await command(request, db, operation)


@app.get("/api/v1/recommendations/projects")
async def recommended_projects(
    request: Request,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    require(1 <= limit <= 100, "INVALID_LIMIT", "Некорректный лимит", 422)
    profile = await internal_call(request, "people", "GET", f"/internal/v1/profiles/{user['id']}")
    member = exists(
        select(Member.id).where(
            Member.project_id == Project.id, Member.user_id == user["id"], Member.left_at.is_(None)
        )
    )
    query = select(Project).where(
        Project.status == "recruiting",
        Project.access_type == "public_open",
        Project.moderation_status == "visible",
        Project.owner_id.is_not(None),
        Project.owner_id != user["id"],
        ~member,
    )
    rows = (await db.scalars(query.order_by(Project.created_at.desc()).limit(500))).all()
    personalized = bool(profile.get("skills") or profile.get("interests"))
    items = []
    cards = await project_cards(db, rows, user)
    for row, card in zip(rows, cards, strict=True):
        if card["free_slots"] == 0:
            continue
        match = (
            recommendation(dto(row), profile)
            if personalized
            else {
                "score": None,
                "components": [],
                "reasons": [],
                "algorithm_version": "weighted-normalized-v1",
            }
        )
        items.append({"project": card, **match})
    items.sort(key=lambda item: item["score"] or -1, reverse=True)
    await enrich_favorites(request, "project", user, [item["project"] for item in items[:limit]])
    return {
        "items": items[:limit],
        "personalized": personalized,
        "fallback_reason": None if personalized else "profile_incomplete",
        "has_more": False,
        "next_cursor": None,
    }


class AccessCheck(Command):
    actor_id: str | None = None
    action: Literal["read", "upload", "link_paper"] = "read"


class BatchRead(Command):
    actor_id: str | None = None
    ids: list[str] = Field(max_length=100)


@app.post(
    "/internal/v1/projects/readable", dependencies=[Depends(internal("people", "publications"))]
)
async def batch_readable(body: BatchRead, db=Depends(session, scope="function")):
    viewer = {"id": body.actor_id} if body.actor_id else None
    rows = (
        await db.scalars(select(Project).where(Project.id.in_(body.ids), visible_query(viewer)))
    ).all()
    return {
        "items": [
            {"id": row.id, "title": row.title, "access_type": row.access_type} for row in rows
        ]
    }


@app.post("/internal/v1/projects/{project_id}/access", dependencies=[Depends(internal())])
async def internal_access(
    project_id: str, body: AccessCheck, db=Depends(session, scope="function")
):
    project = await get_project(db, project_id, {"id": body.actor_id} if body.actor_id else None)
    member = await membership(db, project_id, body.actor_id) if body.actor_id else None
    if body.action == "upload":
        require(
            project.owner_id is not None and project.status != "archived",
            "READ_ONLY",
            "Проект доступен только для чтения",
            403,
        )
        require(member is not None, "FORBIDDEN", "Загружать могут участники", 403)
    if body.action == "link_paper":
        require(
            project.owner_id == body.actor_id, "FORBIDDEN", "Связать работу может руководитель", 403
        )
    return {
        "id": project.id,
        "owner_id": project.owner_id,
        "access_type": project.access_type,
        "status": project.status,
        "can_read": True,
        "is_member": bool(member),
    }


from services.projects.extra import install as install_extra  # noqa: E402
from services.projects.files import install as install_files  # noqa: E402

install_extra(app, get_project, project_view)
install_files(app, get_project, membership)
