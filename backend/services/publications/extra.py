from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import select

from libs.platform.api import Command, paginate
from libs.platform.database import append_event, session
from libs.platform.errors import require
from libs.platform.http import internal, internal_call
from services.publications.models import Paper, PaperAudit


class ResourceInput(Command):
    actor_id: str | None = None


class ModerateInput(Command):
    actor_id: str
    action_id: str
    hidden: bool
    reason: str = Field(min_length=3, max_length=2000)


def install(app, get_paper, view):
    @app.post("/internal/v1/resources/{paper_id}", dependencies=[Depends(internal("activity"))])
    async def read_resource(
        paper_id: str, body: ResourceInput, request: Request, db=Depends(session, scope="function")
    ):
        row = await get_paper(db, paper_id, request, body.actor_id)
        return await view(db, row, body.actor_id)

    @app.get("/internal/v1/admin/catalog", dependencies=[Depends(internal("activity"))])
    async def catalog(
        request: Request, q: str = "", limit: int = 20, db=Depends(session, scope="function")
    ):
        query = select(Paper)
        if q:
            query = query.where(Paper.title.ilike(f"%{q}%"))
        return await paginate(db, query, Paper, request, limit)

    @app.get("/internal/v1/reconcile/{paper_id}", dependencies=[Depends(internal("projects"))])
    async def reconcile(paper_id: str, db=Depends(session, scope="function")):
        row = await db.get(Paper, paper_id)
        require(row is not None, "NOT_FOUND", "Работа не найдена", 404)
        return {
            "paper_id": row.id,
            "project_id": row.project_id,
            "file_id": row.file_id,
            "status": row.status,
        }

    @app.post("/internal/v1/moderate/{paper_id}", dependencies=[Depends(internal("activity"))])
    async def moderate(
        paper_id: str, body: ModerateInput, request: Request, db=Depends(session, scope="function")
    ):
        previous = await db.scalar(select(PaperAudit).where(PaperAudit.action_id == body.action_id))
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
        row = await db.scalar(select(Paper).where(Paper.id == paper_id).with_for_update())
        require(row is not None, "NOT_FOUND", "Работа не найдена", 404)
        row.moderation_status = "hidden" if body.hidden else "visible"
        db.add(
            PaperAudit(
                action_id=body.action_id,
                actor_id=body.actor_id,
                paper_id=paper_id,
                action=row.moderation_status,
                detail={"reason": body.reason},
            )
        )
        await append_event(
            db,
            "publications.paper_hidden",
            paper_id,
            {"paper_id": paper_id, "user_id": row.owner_id, "hidden": body.hidden},
        )
        return {"status": "succeeded", "action_id": body.action_id}
