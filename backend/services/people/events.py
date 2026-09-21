from sqlalchemy import select

from libs.platform.http import internal_call
from services.people.models import Profile

BINDINGS = ["identity.user_registered", "identity.user_blocked", "projects.project_updated"]


async def delete_user(request, db, user_id):
    row = await db.scalar(select(Profile).where(Profile.user_id == user_id).with_for_update())
    if row:
        row.deleted, row.blocked = True, True
        row.first_name, row.last_name = "Удалённый", "пользователь"
        row.bio, row.contacts, row.skills, row.interests, row.experience = "", [], [], [], []
        row.avatar_file_id = None


async def handle(request, db, event):
    data = event["payload"]
    if event["event_type"] == "identity.user_registered":
        row = await db.scalar(select(Profile).where(Profile.user_id == data["user_id"]))
        if row:
            return
        account = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{data['user_id']}"
        )
        if account["status"] not in {"active", "blocked"}:
            return
        db.add(
            Profile(
                user_id=data["user_id"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                university_id=data.get("university_id"),
                faculty_id=data.get("faculty_id"),
                specialty_id=data.get("specialty_id"),
                course=data.get("course"),
                blocked=account["status"] == "blocked",
            )
        )
    elif event["event_type"] == "identity.user_blocked":
        row = await db.scalar(
            select(Profile).where(Profile.user_id == data["user_id"]).with_for_update()
        )
        if row:
            account = await internal_call(
                request, "identity", "GET", f"/internal/v1/users/{data['user_id']}"
            )
            row.blocked = account["status"] != "active"
    elif data.get("status") == "completed":
        for user_id in data.get("member_ids", []):
            row = await db.scalar(
                select(Profile).where(Profile.user_id == user_id).with_for_update()
            )
            if row and not any(
                item.get("project_id") == data["project_id"] for item in row.experience
            ):
                row.experience = [
                    *row.experience,
                    {
                        "id": data["project_id"],
                        "project_id": data["project_id"],
                        "title": data["title"],
                        "completed": True,
                        "source": "platform",
                        "private": data.get("access_type") == "private_invite",
                    },
                ]
