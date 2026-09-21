from typing import Literal

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import false, or_, select

from libs.platform.api import Command, assert_version, command, create_app, dto, paginate
from libs.platform.database import append_event, session, uid
from libs.platform.errors import APIError, require
from libs.platform.http import (
    current_user,
    enrich_favorites,
    internal,
    internal_call,
    optional_user,
)
from libs.platform.schemas import InitializingProfile, Page, ProfileDTO
from services.people.models import DictionaryEntry, DictionarySuggestion, Profile

app = create_app("people")
KINDS = {
    "universities",
    "faculties",
    "departments",
    "specialties",
    "skills",
    "research_areas",
    "research_interests",
    "cities",
    "team_roles",
}


class Contact(Command):
    type: Literal["email", "telegram", "website", "phone", "other"]
    value: str = Field(max_length=500)
    visibility: Literal["public", "authenticated", "private"] = "private"


class Experience(Command):
    id: str | None = None
    title: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=2000)
    completed: bool = False
    source: Literal["self_reported"] = "self_reported"


class ProfilePatch(Command):
    non_nullable_fields = frozenset(
        {"first_name", "last_name", "bio", "skill_ids", "interest_ids", "contacts", "experience"}
    )
    version: int = Field(ge=1)
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=4000)
    university_id: str | None = None
    faculty_id: str | None = None
    department_id: str | None = None
    specialty_id: str | None = None
    city_id: str | None = None
    course: int | None = Field(default=None, ge=1, le=8)
    interest_ids: list[str] | None = Field(default=None, max_length=30)
    skill_ids: list[str] | None = Field(default=None, max_length=50)
    contacts: list[Contact] | None = Field(default=None, max_length=10)
    experience: list[Experience] | None = Field(default=None, max_length=50)
    avatar_file_id: str | None = None


async def profile_by_user(db, user_id: str, lock=False):
    query = select(Profile).where(Profile.user_id == user_id)
    return await db.scalar(query.with_for_update() if lock else query)


def public_profile(row: Profile, viewer: dict | None, own=False) -> dict:
    data = dto(row, exclude={"blocked", "deleted", "contacts"})
    data["id"] = row.user_id
    data["experience"] = [item for item in row.experience if own or not item.get("private")]
    data["contacts"] = [
        c
        for c in row.contacts
        if own or c["visibility"] == "public" or (viewer and c["visibility"] == "authenticated")
    ]
    data["is_favorited"] = None
    data["viewer"] = {"is_self": own, "can_invite": bool(viewer and not own)}
    if own:
        data["missing_fields"] = [
            key
            for key, present in {
                "skills": bool(row.skills),
                "interests": bool(row.interests),
                "bio": bool(row.bio),
            }.items()
            if not present
        ]
        data["completeness"] = round((3 - len(data["missing_fields"])) / 3 * 100)
    return data


async def secure_profiles(request, cards, viewer):
    ids = sorted(
        {
            entry["project_id"]
            for card in cards
            if not card["viewer"]["is_self"]
            for entry in card["experience"]
            if entry.get("source") == "platform" and entry.get("project_id")
        }
    )
    readable = {}
    try:
        for offset in range(0, len(ids), 100):
            result = await internal_call(
                request,
                "projects",
                "POST",
                "/internal/v1/projects/readable",
                json={
                    "actor_id": viewer["id"] if viewer else None,
                    "ids": ids[offset : offset + 100],
                },
            )
            readable.update({item["id"]: item for item in result["items"]})
    except APIError as exc:
        if exc.status != 503:
            raise
        readable = {}
    for card in cards:
        if card["viewer"]["is_self"]:
            continue
        experience = []
        for entry in card["experience"]:
            if entry.get("source") != "platform":
                experience.append(entry)
            elif entry.get("project_id") in readable:
                experience.append({**entry, "title": readable[entry["project_id"]]["title"]})
        card["experience"] = experience
    return cards


async def validate_reference(db, identifier: str, kind: str) -> dict:
    row = await db.scalar(
        select(DictionaryEntry).where(
            DictionaryEntry.id == identifier,
            DictionaryEntry.kind == kind,
            DictionaryEntry.active.is_(True),
        )
    )
    require(row is not None, "INVALID_DICTIONARY_VALUE", "Значение справочника не найдено", 422)
    return {"id": row.id, "name": row.name, "kind": row.kind, "skill_kind": row.skill_kind}


@app.get(
    "/api/v1/me/profile",
    response_model=ProfileDTO | InitializingProfile,
    response_model_exclude_unset=True,
)
async def own_profile(user=Depends(current_user), db=Depends(session, scope="function")):
    row = await profile_by_user(db, user["id"])
    if row is None:
        return JSONResponse({"status": "initializing"}, status_code=202)
    return public_profile(row, user, True)


@app.patch("/api/v1/me/profile", response_model=ProfileDTO, response_model_exclude_unset=True)
async def edit_profile(
    body: ProfilePatch,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    row = await profile_by_user(db, user["id"], True)
    require(row is not None, "PROFILE_INITIALIZING", "Профиль ещё создаётся", 409)
    assert_version(row, body.version)
    values = body.model_dump(exclude_unset=True, exclude={"version", "interest_ids", "skill_ids"})
    references = {
        "university_id": "universities",
        "faculty_id": "faculties",
        "department_id": "departments",
        "specialty_id": "specialties",
        "city_id": "cities",
    }
    for field, kind in references.items():
        if values.get(field):
            await validate_reference(db, values[field], kind)
    university = values.get("university_id", row.university_id)
    faculty = values.get("faculty_id", row.faculty_id)
    if faculty:
        faculty_row = await db.get(DictionaryEntry, faculty)
        require(
            faculty_row is not None and faculty_row.active and faculty_row.parent_id == university,
            "INVALID_FACULTY",
            "Факультет не принадлежит университету",
            422,
        )
    department = values.get("department_id", row.department_id)
    if department:
        department_row = await db.get(DictionaryEntry, department)
        require(
            department_row is not None
            and department_row.active
            and department_row.parent_id == faculty,
            "INVALID_DEPARTMENT",
            "Кафедра не принадлежит факультету",
            422,
        )
    if body.skill_ids is not None:
        row.skills = [
            await validate_reference(db, v, "skills") for v in dict.fromkeys(body.skill_ids)
        ]
    if body.interest_ids is not None:
        row.interests = [
            await validate_reference(db, v, "research_interests")
            for v in dict.fromkeys(body.interest_ids)
        ]
    if values.get("avatar_file_id"):
        await internal_call(
            request,
            "media",
            "POST",
            "/internal/v1/files/claim",
            json={
                "file_id": values["avatar_file_id"],
                "resource_type": "profile",
                "resource_id": row.user_id,
                "actor_id": user["id"],
            },
        )
    for field, value in values.items():
        if field == "experience":
            value = [item for item in row.experience if item.get("source") == "platform"] + [
                {**item, "id": item.get("id") or uid()} for item in value
            ]
        setattr(row, field, value)
    await append_event(
        db, "people.profile_updated", row.user_id, {"user_id": row.user_id, "version": row.version}
    )
    return public_profile(row, user, True)


@app.get("/api/v1/users", response_model=Page[ProfileDTO], response_model_exclude_unset=True)
async def users(
    request: Request,
    q: str = "",
    limit: int = 20,
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    query = select(Profile).where(Profile.blocked.is_(False), Profile.deleted.is_(False))
    if q:
        query = query.where(
            or_(
                (Profile.first_name + " " + Profile.last_name).ilike(f"%{q}%"),
                Profile.first_name.ilike(f"%{q}%"),
                Profile.last_name.ilike(f"%{q}%"),
                Profile.bio.ilike(f"%{q}%"),
            )
        )
    for name in (
        "university_id",
        "faculty_id",
        "department_id",
        "specialty_id",
        "course",
        "city_id",
    ):
        values = request.query_params.getlist(name)
        if values:
            if name == "course":
                require(
                    all(value.isdigit() and 1 <= int(value) <= 8 for value in values),
                    "INVALID_COURSE",
                    "Некорректный курс",
                    422,
                )
            query = query.where(
                getattr(Profile, name).in_([int(v) for v in values] if name == "course" else values)
            )
    for param, field in (("skill_id", Profile.skills), ("interest_id", Profile.interests)):
        values = request.query_params.getlist(param)
        conditions = [field.contains([{"id": value}]) for value in values]
        if conditions:
            query = (
                query.where(*conditions)
                if param == "skill_id" and request.query_params.get("skills_mode") == "all"
                else query.where(or_(*conditions))
            )
    area_ids = request.query_params.getlist("area_id")
    if area_ids:
        interests = (
            await db.scalars(
                select(DictionaryEntry.id).where(
                    DictionaryEntry.kind == "research_interests",
                    DictionaryEntry.active.is_(True),
                    DictionaryEntry.parent_id.in_(area_ids),
                )
            )
        ).all()
        query = query.where(
            or_(
                *[Profile.interests.contains([{"id": identifier}]) for identifier in interests],
                false(),
            )
        )
    if request.query_params.get("has_project_experience") == "true":
        query = query.where(Profile.experience != [])
    result = await paginate(db, query, Profile, request, limit)
    rows = {
        row.id: row
        for row in (
            await db.scalars(
                select(Profile).where(Profile.id.in_([item["id"] for item in result["items"]]))
            )
        ).all()
    }
    result["items"] = await secure_profiles(
        request,
        [
            public_profile(
                rows[item["id"]], user, bool(user and rows[item["id"]].user_id == user["id"])
            )
            for item in result["items"]
        ],
        user,
    )
    await enrich_favorites(request, "user", user, result["items"])
    return result


@app.get("/api/v1/users/{user_id}", response_model=ProfileDTO, response_model_exclude_unset=True)
async def user_profile(
    user_id: str,
    request: Request,
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    row = await profile_by_user(db, user_id)
    require(
        row is not None and not row.blocked and not row.deleted,
        "NOT_FOUND",
        "Профиль не найден",
        404,
    )
    cards = await secure_profiles(
        request, [public_profile(row, user, bool(user and user["id"] == user_id))], user
    )
    return (await enrich_favorites(request, "user", user, cards))[0]


@app.get("/api/v1/recommendations/users")
async def recommended_users(
    request: Request,
    limit: int = 6,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    own = await profile_by_user(db, user["id"])
    query = select(Profile).where(
        Profile.user_id != user["id"], Profile.blocked.is_(False), Profile.deleted.is_(False)
    )
    if own and own.interests:
        query = query.where(
            or_(*[Profile.interests.contains([{"id": item["id"]}]) for item in own.interests])
        )
    result = await paginate(db, query, Profile, request, limit)
    result["items"] = [
        public_profile(await profile_by_user(db, item["user_id"]), user) for item in result["items"]
    ]
    await secure_profiles(request, result["items"], user)
    await enrich_favorites(request, "user", user, result["items"])
    return result


@app.get("/api/v1/users/{user_id}/avatar-url")
async def avatar_url(
    user_id: str, request: Request, response: Response, db=Depends(session, scope="function")
):
    row = await profile_by_user(db, user_id)
    require(
        row is not None and not row.blocked and not row.deleted and row.avatar_file_id is not None,
        "NOT_FOUND",
        "Аватар не найден",
        404,
    )
    response.headers["Cache-Control"] = "no-store"
    return await internal_call(
        request,
        "media",
        "POST",
        "/internal/v1/files/url",
        json={
            "file_id": row.avatar_file_id,
            "resource_type": "profile",
            "resource_id": user_id,
            "mode": "inline",
        },
    )


class SuggestionInput(Command):
    kind: str
    name: str = Field(min_length=2, max_length=200)
    parent_id: str | None = None
    comment: str = Field(default="", max_length=1000)


@app.post("/api/v1/dictionaries/suggestions", status_code=201)
async def suggest(
    body: SuggestionInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    require(
        body.kind in KINDS - {"team_roles"}, "INVALID_DICTIONARY", "Недопустимый справочник", 422
    )

    async def operation():
        row = DictionarySuggestion(id=uid(), user_id=user["id"], **body.model_dump())
        db.add(row)
        await db.flush()
        return dto(row)

    return await command(request, db, operation, 201)


@app.get("/api/v1/me/dictionary-suggestions")
async def own_suggestions(
    request: Request,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    return await paginate(
        db,
        select(DictionarySuggestion).where(DictionarySuggestion.user_id == user["id"]),
        DictionarySuggestion,
        request,
        limit,
    )


@app.get("/api/v1/dictionaries/{kind}")
async def dictionary(
    kind: str, request: Request, q: str = "", limit: int = 20, db=Depends(session, scope="function")
):
    require(kind in KINDS, "NOT_FOUND", "Справочник не найден", 404)
    query = select(DictionaryEntry).where(
        DictionaryEntry.kind == kind, DictionaryEntry.active.is_(True)
    )
    if q:
        query = query.where(DictionaryEntry.name.ilike(f"%{q}%"))
    parent = request.query_params.get("university_id") or request.query_params.get("faculty_id")
    if parent:
        query = query.where(DictionaryEntry.parent_id == parent)
    if request.query_params.get("skill_kind"):
        query = query.where(DictionaryEntry.skill_kind == request.query_params["skill_kind"])
    return await paginate(db, query, DictionaryEntry, request, limit)


@app.get("/internal/v1/profiles/{user_id}", dependencies=[Depends(internal())])
async def internal_profile(user_id: str, db=Depends(session, scope="function")):
    row = await profile_by_user(db, user_id)
    require(
        row is not None and not row.deleted and not row.blocked,
        "NOT_FOUND",
        "Профиль не найден",
        404,
    )
    data = public_profile(row, None)
    data["experience"] = row.experience
    data.pop("contacts", None)
    return data


class BatchInput(Command):
    ids: list[str] = Field(max_length=100)


@app.post("/internal/v1/profiles/batch", dependencies=[Depends(internal())])
async def batch_profiles(body: BatchInput, db=Depends(session, scope="function")):
    rows = (
        await db.scalars(
            select(Profile).where(
                Profile.user_id.in_(body.ids),
                Profile.blocked.is_(False),
                Profile.deleted.is_(False),
            )
        )
    ).all()
    return {"items": [public_profile(row, None) for row in rows]}


@app.post("/internal/v1/dictionaries/validate", dependencies=[Depends(internal())])
async def validate_dictionary(body: BatchInput, db=Depends(session, scope="function")):
    rows = (
        await db.scalars(
            select(DictionaryEntry).where(
                DictionaryEntry.id.in_(body.ids), DictionaryEntry.active.is_(True)
            )
        )
    ).all()
    require(
        len(rows) == len(set(body.ids)),
        "INVALID_DICTIONARY_VALUE",
        "Значение справочника не найдено",
        422,
    )
    return {"items": [dto(row) for row in rows]}


from services.people.extra import install as install_extra  # noqa: E402

install_extra(app, profile_by_user, public_profile, KINDS, secure_profiles)
