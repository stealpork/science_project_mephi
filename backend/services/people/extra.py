from typing import Literal

from fastapi import Depends, Request
from pydantic import Field
from sqlalchemy import select

from libs.platform.api import Command, assert_version, dto, paginate
from libs.platform.database import append_event, session, uid
from libs.platform.errors import require
from libs.platform.http import internal
from services.people.models import DictionaryEntry, DictionarySuggestion


class ReadInput(Command):
    actor_id: str | None = None


class DictionaryInput(Command):
    name: str = Field(min_length=2, max_length=200)
    parent_id: str | None = None
    skill_kind: str | None = None


class DictionaryPatch(Command):
    version: int
    name: str | None = Field(default=None, min_length=2, max_length=200)
    active: bool | None = None


class Decision(Command):
    target_id: str | None = None
    reason: str = Field(default="", max_length=2000)


def install(app, profile_by_user, public_profile, kinds, secure_profiles):
    @app.post("/internal/v1/resources/{user_id}", dependencies=[Depends(internal("activity"))])
    async def read_resource(
        user_id: str, body: ReadInput, request: Request, db=Depends(session, scope="function")
    ):
        row = await profile_by_user(db, user_id)
        require(
            row is not None and not row.blocked and not row.deleted,
            "NOT_FOUND",
            "Профиль не найден",
            404,
        )
        data = public_profile(row, None)
        data.pop("contacts", None)
        viewer = {"id": body.actor_id} if body.actor_id else None
        return (await secure_profiles(request, [data], viewer))[0]

    async def create_entry(db, kind, body):
        require(kind in kinds, "NOT_FOUND", "Справочник не найден", 404)
        if body.parent_id:
            parent = await db.get(DictionaryEntry, body.parent_id)
            expected = {
                "faculties": "universities",
                "departments": "faculties",
                "research_interests": "research_areas",
            }.get(kind)
            require(
                parent is not None
                and parent.active
                and (expected is None or parent.kind == expected),
                "INVALID_PARENT",
                "Недопустимый родитель",
                422,
            )
        require(
            kind not in {"faculties", "departments"} or body.parent_id is not None,
            "PARENT_REQUIRED",
            "Укажите родителя",
            422,
        )
        normalized = " ".join(body.name.casefold().split())
        existing = await db.scalar(
            select(DictionaryEntry).where(
                DictionaryEntry.kind == kind,
                DictionaryEntry.normalized_name == normalized,
                DictionaryEntry.parent_key == (body.parent_id or ""),
            )
        )
        if existing:
            return existing
        row = DictionaryEntry(
            id=uid(),
            kind=kind,
            name=body.name,
            normalized_name=normalized,
            parent_id=body.parent_id,
            parent_key=body.parent_id or "",
            skill_kind=body.skill_kind,
        )
        db.add(row)
        await db.flush()
        return row

    @app.post("/internal/v1/dictionaries/{kind}", dependencies=[Depends(internal("activity"))])
    async def add_entry(kind: str, body: DictionaryInput, db=Depends(session, scope="function")):
        return dto(await create_entry(db, kind, body))

    @app.patch(
        "/internal/v1/dictionaries/{kind}/{entry_id}", dependencies=[Depends(internal("activity"))]
    )
    async def edit_entry(
        kind: str, entry_id: str, body: DictionaryPatch, db=Depends(session, scope="function")
    ):
        row = await db.scalar(
            select(DictionaryEntry)
            .where(DictionaryEntry.kind == kind, DictionaryEntry.id == entry_id)
            .with_for_update()
        )
        require(row is not None, "NOT_FOUND", "Значение не найдено", 404)
        assert_version(row, body.version)
        if body.name is not None:
            row.name, row.normalized_name = body.name, " ".join(body.name.casefold().split())
        if body.active is not None:
            row.active = body.active
        return dto(row)

    @app.get("/internal/v1/dictionary-suggestions", dependencies=[Depends(internal("activity"))])
    async def suggestions(
        request: Request,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 20,
        db=Depends(session, scope="function"),
    ):
        query = select(DictionarySuggestion)
        if status:
            query = query.where(DictionarySuggestion.status == status)
        if kind:
            query = query.where(DictionarySuggestion.kind == kind)
        return await paginate(db, query, DictionarySuggestion, request, limit)

    @app.post(
        "/internal/v1/dictionary-suggestions/{suggestion_id}/{decision}",
        dependencies=[Depends(internal("activity"))],
    )
    async def decide(
        suggestion_id: str,
        decision: Literal["approve", "merge", "reject"],
        body: Decision,
        db=Depends(session, scope="function"),
    ):
        row = await db.scalar(
            select(DictionarySuggestion)
            .where(DictionarySuggestion.id == suggestion_id)
            .with_for_update()
        )
        require(row is not None, "NOT_FOUND", "Предложение не найдено", 404)
        if row.status != "pending":
            return dto(row)
        if decision == "approve":
            entry = await create_entry(
                db, row.kind, DictionaryInput(name=row.name, parent_id=row.parent_id)
            )
            row.status, row.resolved_id = "approved", entry.id
        elif decision == "merge":
            entry = await db.get(DictionaryEntry, body.target_id) if body.target_id else None
            require(
                entry is not None and entry.kind == row.kind and entry.active,
                "INVALID_TARGET",
                "Недопустимое значение",
                422,
            )
            assert entry is not None
            row.status, row.resolved_id = "merged", entry.id
        else:
            require(bool(body.reason), "REASON_REQUIRED", "Укажите причину", 422)
            row.status = "rejected"
        row.decision_reason = body.reason
        await append_event(
            db,
            "people.dictionary_decided",
            row.id,
            {"suggestion_id": row.id, "user_id": row.user_id, "status": row.status},
        )
        return dto(row)
