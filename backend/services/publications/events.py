from sqlalchemy import select

from libs.platform.errors import APIError
from libs.platform.http import internal_call
from services.publications.models import Author, Paper

BINDINGS = ["activity.favorite_changed"]


async def handle(request, db, event):
    data = event["payload"]
    if data["type"] != "paper":
        return
    paper = await db.scalar(select(Paper).where(Paper.id == data["target_id"]).with_for_update())
    if paper:
        value = await internal_call(
            request, "activity", "GET", f"/internal/v1/favorites/paper/{paper.id}/count"
        )
        paper.saves_count = value["count"]


async def delete_user(request, db, user_id):
    papers = (
        await db.scalars(
            select(Paper).where(Paper.owner_id == user_id).order_by(Paper.id).with_for_update()
        )
    ).all()
    for paper in papers:
        candidates = (
            await db.scalars(
                select(Author)
                .where(
                    Author.paper_id == paper.id,
                    Author.user_id.is_not(None),
                    Author.user_id != user_id,
                )
                .order_by(Author.access_granted_at, Author.id)
            )
        ).all()
        paper.owner_id = None
        for candidate in candidates:
            account = await internal_call(
                request, "identity", "GET", f"/internal/v1/users/{candidate.user_id}"
            )
            if account["status"] != "active":
                continue
            if paper.project_id:
                try:
                    await internal_call(
                        request,
                        "projects",
                        "POST",
                        f"/internal/v1/projects/{paper.project_id}/access",
                        json={"actor_id": candidate.user_id, "action": "read"},
                    )
                except APIError as exc:
                    if exc.status in {403, 404}:
                        continue
                    raise
            paper.owner_id = candidate.user_id
            break
    authors = (await db.scalars(select(Author).where(Author.user_id == user_id))).all()
    for author in authors:
        author.user_id, author.access_granted_at = None, None
        author.display_name = author.display_name[:175] + " (аккаунт удалён)"
