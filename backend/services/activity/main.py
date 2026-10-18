from datetime import datetime
from typing import Literal

from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from libs.platform.api import Command, assert_version, command, create_app, dto, paginate
from libs.platform.database import append_event, now, session, uid
from libs.platform.errors import APIError, require
from libs.platform.http import admin, current_user, internal, internal_call, moderator
from services.activity.models import Audit, Favorite, ModerationAction, Notification, Report

app = create_app("activity")
TargetType = Literal["user", "project", "paper"]
OWNERS = {"user": "people", "project": "projects", "paper": "publications"}


async def resource(request, kind, identifier, actor_id):
    return await internal_call(
        request,
        OWNERS[kind],
        "POST",
        f"/internal/v1/resources/{identifier}",
        json={"actor_id": actor_id},
    )


@app.put("/api/v1/favorites/{type}/{target_id}", status_code=204)
async def add_favorite(
    type: TargetType,
    target_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    await resource(request, type, target_id, user["id"])
    statement = (
        insert(Favorite)
        .values(id=uid(), user_id=user["id"], target_type=type, target_id=target_id)
        .on_conflict_do_nothing(index_elements=["user_id", "target_type", "target_id"])
        .returning(Favorite.id)
    )
    created = await db.scalar(statement)
    if created:
        await append_event(
            db,
            "activity.favorite_changed",
            target_id,
            {"type": type, "target_id": target_id, "delta": 1},
        )


@app.delete("/api/v1/favorites/{type}/{target_id}", status_code=204)
async def remove_favorite(
    type: TargetType,
    target_id: str,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    deleted = await db.scalar(
        delete(Favorite)
        .where(
            Favorite.user_id == user["id"],
            Favorite.target_type == type,
            Favorite.target_id == target_id,
        )
        .returning(Favorite.id)
    )
    if deleted:
        await append_event(
            db,
            "activity.favorite_changed",
            target_id,
            {"type": type, "target_id": target_id, "delta": -1},
        )


@app.get("/api/v1/favorites")
async def favorites(
    type: TargetType,
    request: Request,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    result = await paginate(
        db,
        select(Favorite).where(Favorite.user_id == user["id"], Favorite.target_type == type),
        Favorite,
        request,
        limit,
    )
    items = []
    for item in result["items"]:
        try:
            card = await resource(request, type, item["target_id"], user["id"])
            availability = "available"
        except APIError as exc:
            if exc.status in {403, 404}:
                card, availability = None, "unavailable"
            elif exc.status == 503:
                card, availability = None, "temporarily_unavailable"
            else:
                raise
        items.append(
            {
                "type": type,
                "target_id": item["target_id"],
                "saved_at": item["created_at"],
                "availability": availability,
                "card": card,
            }
        )
    result["items"] = items
    return result


class FavoriteBatch(Command):
    user_id: str
    type: TargetType
    ids: list[str] = Field(max_length=100)


@app.get(
    "/internal/v1/favorites/paper/{paper_id}/count",
    dependencies=[Depends(internal("publications"))],
)
async def paper_favorite_count(paper_id: str, db=Depends(session, scope="function")):
    value = await db.scalar(
        select(func.count())
        .select_from(Favorite)
        .where(Favorite.target_type == "paper", Favorite.target_id == paper_id)
    )
    return {"count": value or 0}


@app.post(
    "/internal/v1/favorites/batch",
    dependencies=[Depends(internal("people", "projects", "publications"))],
)
async def favorite_batch(body: FavoriteBatch, db=Depends(session, scope="function")):
    identifiers = (
        await db.scalars(
            select(Favorite.target_id).where(
                Favorite.user_id == body.user_id,
                Favorite.target_type == body.type,
                Favorite.target_id.in_(body.ids),
            )
        )
    ).all()
    return {"saved_ids": list(identifiers)}


@app.get("/api/v1/notifications/unread-count")
async def unread_count(user=Depends(current_user), db=Depends(session, scope="function")):
    return {
        "count": await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user["id"], Notification.read_at.is_(None))
        )
        or 0
    }


@app.get("/api/v1/notifications")
async def notifications(
    request: Request,
    unread_only: bool = False,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    query = select(Notification).where(Notification.user_id == user["id"])
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    result = await paginate(db, query, Notification, request, limit)
    for item in result["items"]:
        item["target"] = {"type": item.pop("target_type"), "id": item.pop("target_id")}
        item["action"] = "view_invitation" if item["target"]["type"] == "invitation" else "view"
        if item["target"]["type"] in OWNERS:
            try:
                await resource(request, item["target"]["type"], item["target"]["id"], user["id"])
            except APIError as exc:
                if exc.status in {403, 404}:
                    item["title"], item["body"], item["action"] = (
                        "Материал больше недоступен",
                        "",
                        "unavailable",
                    )
                elif exc.status == 503:
                    item["action"] = "temporarily_unavailable"
                else:
                    raise
    return result


@app.post("/api/v1/notifications/{notification_id}/read", status_code=204)
async def read_notification(
    notification_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    row = await db.get(Notification, notification_id)
    require(
        row is not None and row.user_id == user["id"], "NOT_FOUND", "Уведомление не найдено", 404
    )
    row.read_at = row.read_at or now()


class ReadAll(Command):
    before: datetime


@app.post("/api/v1/notifications/read-all", status_code=204)
async def read_all(
    body: ReadAll, user=Depends(current_user), db=Depends(session, scope="function")
):
    require(body.before.tzinfo is not None, "INVALID_DATE", "Укажите часовой пояс", 422)
    await db.execute(
        update(Notification)
        .where(
            Notification.user_id == user["id"],
            Notification.created_at <= min(body.before, now()),
            Notification.read_at.is_(None),
        )
        .values(read_at=now())
    )


class ReportInput(Command):
    target_type: TargetType
    target_id: str
    reason_code: Literal["spam", "abuse", "plagiarism", "other"]
    description: str = Field(min_length=10, max_length=5000)


@app.post("/api/v1/reports", status_code=201)
async def report(
    body: ReportInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        await resource(request, body.target_type, body.target_id, user["id"])
        row = Report(id=uid(), user_id=user["id"], **body.model_dump())
        db.add(row)
        await db.flush()
        return dto(row)

    return await command(request, db, operation, 201)


@app.get("/api/v1/admin/reports")
async def reports(
    request: Request,
    status: str | None = None,
    target_type: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    moderator(user)
    query = select(Report)
    if status:
        query = query.where(Report.status == status)
    if target_type:
        query = query.where(Report.target_type == target_type)
    return await paginate(db, query, Report, request, limit)


@app.get("/api/v1/admin/reports/{report_id}")
async def report_detail(
    report_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    moderator(user)
    row = await db.get(Report, report_id)
    require(row is not None, "NOT_FOUND", "Жалоба не найдена", 404)
    return dto(row)


class ReportDecision(Command):
    version: int
    status: Literal["in_review", "resolved", "dismissed"]
    resolution: str = Field(min_length=3, max_length=5000)


@app.patch("/api/v1/admin/reports/{report_id}")
async def decide_report(
    report_id: str,
    body: ReportDecision,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    moderator(user)
    row = await db.scalar(select(Report).where(Report.id == report_id).with_for_update())
    require(row is not None, "NOT_FOUND", "Жалоба не найдена", 404)
    assert_version(row, body.version)
    row.status, row.resolution = body.status, body.resolution
    db.add(
        Audit(
            actor_id=user["id"],
            action="report_decided",
            target_type="report",
            target_id=report_id,
            detail={"status": body.status, "resolution": body.resolution},
        )
    )
    return dto(row)


@app.get("/api/v1/admin/users")
async def admin_users(request: Request, user=Depends(current_user)):
    moderator(user)
    return await internal_call(
        request, "identity", "GET", "/internal/v1/users", params=dict(request.query_params)
    )


@app.get("/api/v1/admin/users/{user_id}")
async def admin_user(user_id: str, request: Request, user=Depends(current_user)):
    moderator(user)
    return await internal_call(request, "identity", "GET", f"/internal/v1/users/{user_id}")


@app.get("/api/v1/admin/projects")
@app.get("/api/v1/admin/papers")
async def admin_catalog(
    request: Request, user=Depends(current_user), db=Depends(session, scope="function")
):
    kind = request.url.path.rsplit("/", 1)[1]
    moderator(user)
    result = await internal_call(
        request,
        "projects" if kind == "projects" else "publications",
        "GET",
        "/internal/v1/admin/catalog",
        params=dict(request.query_params),
    )
    db.add(
        Audit(
            actor_id=user["id"], action="admin_catalog_read", target_type=kind, target_id="catalog"
        )
    )
    return result


class ModerationInput(Command):
    reason: str = Field(min_length=3, max_length=2000)


async def perform_action(request, action: ModerationAction):
    if action.target_type == "user":
        return await internal_call(
            request,
            "identity",
            "POST",
            f"/internal/v1/users/{action.target_id}/block",
            json={
                "action_id": action.id,
                "actor_id": action.actor_id,
                "blocked": action.operation == "block",
                "reason": action.reason,
            },
        )
    return await internal_call(
        request,
        OWNERS[action.target_type],
        "POST",
        f"/internal/v1/moderate/{action.target_id}",
        json={
            "action_id": action.id,
            "actor_id": action.actor_id,
            "hidden": action.operation == "hide",
            "reason": action.reason,
        },
    )


@app.post("/api/v1/admin/users/{target_id}/{operation}", status_code=202)
@app.post("/api/v1/admin/projects/{target_id}/{operation}", status_code=202)
@app.post("/api/v1/admin/papers/{target_id}/{operation}", status_code=202)
async def moderate_resource(
    target_id: str,
    operation: Literal["block", "unblock", "hide", "restore"],
    body: ModerationInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    kind = request.url.path.split("/")[-3]
    moderator(user)
    require(
        (kind == "users" and operation in {"block", "unblock"})
        or (kind != "users" and operation in {"hide", "restore"}),
        "INVALID_ACTION",
        "Недопустимое действие",
        422,
    )

    async def operation_body():
        row = ModerationAction(
            id=uid(),
            actor_id=user["id"],
            target_type={"users": "user", "projects": "project", "papers": "paper"}[kind],
            target_id=target_id,
            operation=operation,
            reason=body.reason,
        )
        db.add(row)
        await db.flush()
        await append_event(db, "activity.action_requested", row.id, {"action_id": row.id})
        try:
            row.result = await perform_action(request, row)
            row.status = "succeeded"
        except APIError as exc:
            if exc.status != 503:
                row.status, row.result = "failed", {"code": exc.code}
        db.add(
            Audit(
                actor_id=user["id"],
                action=operation,
                target_type=row.target_type,
                target_id=target_id,
                detail={"action_id": row.id, "reason": body.reason},
            )
        )
        return {"action_id": row.id, "status": row.status}

    return await command(request, db, operation_body, 202)


@app.get("/api/v1/admin/actions/{action_id}")
async def action_status(
    action_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    moderator(user)
    row = await db.get(ModerationAction, action_id)
    require(row is not None, "NOT_FOUND", "Действие не найдено", 404)
    return {"action_id": row.id, "status": row.status, "result": row.result}


@app.get("/api/v1/admin/audit")
async def audit(
    request: Request,
    limit: int = 20,
    actor_id: str | None = None,
    target_id: str | None = None,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    moderator(user)
    query = select(Audit)
    if actor_id:
        query = query.where(Audit.actor_id == actor_id)
    if target_id:
        query = query.where(Audit.target_id == target_id)
    return await paginate(db, query, Audit, request, limit)


@app.get("/api/v1/admin/settings/project-files")
async def file_policy(request: Request, user=Depends(current_user)):
    admin(user)
    return await internal_call(request, "projects", "GET", "/internal/v1/settings/project-files")


class PolicyInput(Command):
    version: int
    max_files_per_project: int = Field(ge=1, le=1000)
    reason: str = Field(min_length=3, max_length=2000)


@app.patch("/api/v1/admin/settings/project-files")
async def file_policy_patch(
    body: PolicyInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    admin(user)
    result = await internal_call(
        request,
        "projects",
        "PATCH",
        "/internal/v1/settings/project-files",
        json=body.model_dump(exclude={"reason"}),
    )
    db.add(
        Audit(
            actor_id=user["id"],
            action="file_policy_changed",
            target_type="settings",
            target_id="project-files",
            detail={"max_files_per_project": body.max_files_per_project, "reason": body.reason},
        )
    )
    return result


@app.get("/api/v1/admin/dictionary-suggestions")
async def dictionary_suggestions(request: Request, user=Depends(current_user)):
    moderator(user)
    return await internal_call(
        request,
        "people",
        "GET",
        "/internal/v1/dictionary-suggestions",
        params=dict(request.query_params),
    )


class SuggestionDecision(Command):
    target_id: str | None = None
    reason: str = Field(default="", max_length=2000)


@app.post("/api/v1/admin/dictionary-suggestions/{suggestion_id}/{decision}")
async def decide_suggestion(
    suggestion_id: str,
    decision: Literal["approve", "merge", "reject"],
    body: SuggestionDecision,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    moderator(user)

    async def operation():
        result = await internal_call(
            request,
            "people",
            "POST",
            f"/internal/v1/dictionary-suggestions/{suggestion_id}/{decision}",
            json=body.model_dump(),
        )
        db.add(
            Audit(
                actor_id=user["id"],
                action="dictionary_" + decision,
                target_type="dictionary_suggestion",
                target_id=suggestion_id,
                detail=body.model_dump(),
            )
        )
        return result

    return await command(request, db, operation)


class DictionaryInput(Command):
    name: str = Field(min_length=2, max_length=200)
    parent_id: str | None = None
    skill_kind: str | None = None


@app.post("/api/v1/admin/dictionaries/{kind}", status_code=201)
async def create_dictionary(
    kind: str,
    body: DictionaryInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    admin(user)

    async def operation():
        result = await internal_call(
            request, "people", "POST", f"/internal/v1/dictionaries/{kind}", json=body.model_dump()
        )
        db.add(
            Audit(
                actor_id=user["id"],
                action="dictionary_created",
                target_type=kind,
                target_id=result["id"],
            )
        )
        return result

    return await command(request, db, operation, 201)


class DictionaryPatch(Command):
    version: int
    name: str | None = Field(default=None, min_length=2, max_length=200)
    active: bool | None = None


@app.patch("/api/v1/admin/dictionaries/{kind}/{entry_id}")
async def edit_dictionary(
    kind: str,
    entry_id: str,
    body: DictionaryPatch,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    admin(user)
    result = await internal_call(
        request,
        "people",
        "PATCH",
        f"/internal/v1/dictionaries/{kind}/{entry_id}",
        json=body.model_dump(exclude_unset=True),
    )
    db.add(
        Audit(
            actor_id=user["id"], action="dictionary_updated", target_type=kind, target_id=entry_id
        )
    )
    return result
