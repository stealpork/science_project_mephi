from sqlalchemy import literal, or_, select, update

from libs.platform.database import append_event, now
from libs.platform.http import internal_call
from services.projects.models import (
    Application,
    FileReservation,
    Invitation,
    Member,
    Project,
    ProjectFile,
)

BINDINGS = [
    "publications.paper_changed",
    "media.file_rejected",
    "media.upload_cancelled",
    "media.upload_expired",
]


async def handle(request, db, event):
    if event["event_type"].startswith("media."):
        data = event["payload"]
        if data.get("reservation_id"):
            await db.execute(
                update(FileReservation)
                .where(
                    FileReservation.id == data["reservation_id"],
                    FileReservation.file_id == data["file_id"],
                    FileReservation.status == "pending",
                )
                .values(status="cancelled")
            )
        return
    paper_id = event["payload"]["paper_id"]
    state = await internal_call(
        request, "publications", "GET", f"/internal/v1/reconcile/{paper_id}"
    )
    await db.execute(
        update(ProjectFile)
        .where(
            ProjectFile.paper_id == paper_id,
            or_(
                ProjectFile.project_id != state["project_id"],
                ProjectFile.file_id != state["file_id"],
                literal(state["status"] == "archived"),
            ),
        )
        .values(active=False)
    )


async def delete_user(request, db, user_id):
    projects = (
        await db.scalars(
            select(Project)
            .where(Project.owner_id == user_id)
            .order_by(Project.id)
            .with_for_update()
        )
    ).all()
    for project in projects:
        candidates = (
            await db.scalars(
                select(Member)
                .where(
                    Member.project_id == project.id,
                    Member.user_id != user_id,
                    Member.left_at.is_(None),
                )
                .order_by(Member.created_at, Member.id)
            )
        ).all()
        successor = None
        for candidate in candidates:
            account = await internal_call(
                request, "identity", "GET", f"/internal/v1/users/{candidate.user_id}"
            )
            if account["status"] == "active":
                successor = candidate
                break
        project.owner_id = successor.user_id if successor else None
        if successor:
            successor.role = "leader"
        else:
            await db.execute(
                update(Application)
                .where(Application.project_id == project.id, Application.status == "pending")
                .values(status="cancelled")
            )
            await db.execute(
                update(Invitation)
                .where(Invitation.project_id == project.id, Invitation.status == "pending")
                .values(status="revoked")
            )
        await append_event(
            db,
            "projects.owner_changed",
            project.id,
            {"project_id": project.id, "user_id": project.owner_id},
        )
    await db.execute(
        update(Member)
        .where(Member.user_id == user_id, Member.left_at.is_(None))
        .values(left_at=now())
    )
    await db.execute(
        update(Application)
        .where(Application.user_id == user_id, Application.status == "pending")
        .values(status="cancelled")
    )
    await db.execute(
        update(Invitation)
        .where(Invitation.user_id == user_id, Invitation.status == "pending")
        .values(status="revoked")
    )
    await db.execute(
        update(FileReservation)
        .where(FileReservation.actor_id == user_id, FileReservation.status == "pending")
        .values(status="cancelled")
    )
