from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import select

from libs.platform.api import Command, paginate
from libs.platform.database import append_event, session
from libs.platform.errors import require
from libs.platform.http import internal, internal_call
from services.projects.models import Project, ProjectAudit


class ResourceInput(Command):
    actor_id: str | None = None


class ModerateInput(Command):
    actor_id: str
    action_id: str
    hidden: bool
    reason: str = Field(min_length=3, max_length=2000)


def install(app, get_project, project_view):
    @app.post("/internal/v1/resources/{project_id}", dependencies=[Depends(internal("activity"))])
    async def read_resource(
        project_id: str, body: ResourceInput, db=Depends(session, scope="function")
    ):
        user = {"id": body.actor_id} if body.actor_id else None
        row = await get_project(db, project_id, user)
        return await project_view(db, row, user)

    @app.get("/internal/v1/admin/catalog", dependencies=[Depends(internal("activity"))])
    async def catalog(
        request: Request, q: str = "", limit: int = 20, db=Depends(session, scope="function")
    ):
        query = select(Project)
        if q:
            query = query.where(Project.title.ilike(f"%{q}%"))
        return await paginate(db, query, Project, request, limit)

    @app.post("/internal/v1/moderate/{project_id}", dependencies=[Depends(internal("activity"))])
    async def moderate(
        project_id: str,
        body: ModerateInput,
        request: Request,
        db=Depends(session, scope="function"),
    ):
        previous = await db.scalar(
            select(ProjectAudit).where(ProjectAudit.action_id == body.action_id)
        )
        if previous:
            return {"status": "succeeded", "action_id": body.action_id}
        actor = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{body.actor_id}"
        )
        require(
            actor["status"] == "active" and set(actor["roles"]) & {"moderator", "admin"},
            "FORBIDDEN",
            "Нет полномочий",
            403,
        )
        row = await db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        require(row is not None, "NOT_FOUND", "Проект не найден", 404)
        row.moderation_status = "hidden" if body.hidden else "visible"
        db.add(
            ProjectAudit(
                action_id=body.action_id,
                actor_id=body.actor_id,
                project_id=project_id,
                action=row.moderation_status,
                reason=body.reason,
            )
        )
        await append_event(
            db,
            "projects.project_moderated",
            project_id,
            {"project_id": project_id, "user_id": row.owner_id, "hidden": body.hidden},
        )
        return {"status": "succeeded", "action_id": body.action_id}
