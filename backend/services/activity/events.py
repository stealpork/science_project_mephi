from sqlalchemy import select

from libs.platform.database import append_event
from libs.platform.errors import APIError
from services.activity.models import Favorite, ModerationAction, Notification

BINDINGS = [
    "projects.#",
    "publications.#",
    "people.#",
    "media.file_rejected",
    "activity.action_requested",
]


async def handle(request, db, event):
    event_type, data = event["event_type"], event["payload"]
    if event_type == "activity.action_requested":
        from services.activity.main import perform_action

        row = await db.scalar(
            select(ModerationAction)
            .where(ModerationAction.id == data["action_id"])
            .with_for_update()
        )
        if row and row.status == "pending":
            try:
                row.result = await perform_action(request, row)
                row.status = "succeeded"
            except APIError as exc:
                if exc.status == 503:
                    raise
                row.status, row.result = "failed", {"code": exc.code}
        return
    recipients, title = [], ""
    target_type, target_id = "project", data.get("project_id")
    if event_type == "projects.application_submitted":
        recipients, title = [data["owner_id"]], "Новая заявка в ваш проект"
    elif event_type == "projects.application_decided":
        recipients, title = [data["user_id"]], "Заявка рассмотрена: " + data["status"]
    elif event_type == "projects.invitation_created":
        recipients, title = [data["user_id"]], "Вас приглашают в исследовательскую команду"
        target_type, target_id = "invitation", data["invitation_id"]
    elif event_type == "projects.invitation_decided":
        recipients, title = [data["owner_id"]], "Приглашение рассмотрено: " + data["status"]
    elif event_type in {"projects.member_joined", "projects.member_left", "projects.owner_changed"}:
        recipients, title = (
            [data.get("user_id")],
            {
                "projects.member_joined": "Вы стали участником команды",
                "projects.member_left": "Вы больше не участник команды",
                "projects.owner_changed": "Вам передано руководство проектом",
            }[event_type],
        )
    elif event_type == "projects.project_updated" and data.get("member_ids"):
        recipients, title = data["member_ids"], "Изменился статус вашего проекта"
    elif event_type in {"publications.paper_hidden", "projects.project_moderated"}:
        recipients, title = [data.get("user_id")], "Изменена видимость вашего материала"
        if event_type.startswith("publications"):
            target_type, target_id = "paper", data["paper_id"]
    elif event_type == "people.dictionary_decided":
        recipients, title, target_type, target_id = (
            [data["user_id"]],
            "Рассмотрено предложение справочника",
            "suggestion",
            data["suggestion_id"],
        )
    elif event_type == "media.file_rejected":
        recipients, title, target_type, target_id = (
            [data["user_id"]],
            "Файл не прошёл проверку",
            "upload",
            data["file_id"],
        )
    for user_id in set(recipients) - {None}:
        db.add(
            Notification(
                user_id=user_id,
                event_id=event["event_id"],
                type=event_type,
                title=title,
                body="",
                target_type=target_type,
                target_id=target_id,
            )
        )


async def reconcile(request, db):
    rows = (
        await db.scalars(
            select(ModerationAction).where(ModerationAction.status == "pending").limit(20)
        )
    ).all()
    for row in rows:
        await append_event(db, "activity.action_requested", row.id, {"action_id": row.id})


async def delete_user(request, db, user_id):
    favorites = (await db.scalars(select(Favorite).where(Favorite.user_id == user_id))).all()
    for favorite in favorites:
        await append_event(
            db,
            "activity.favorite_changed",
            favorite.target_id,
            {"type": favorite.target_type, "target_id": favorite.target_id, "delta": -1},
        )
        await db.delete(favorite)
    from sqlalchemy import delete

    await db.execute(delete(Notification).where(Notification.user_id == user_id))
