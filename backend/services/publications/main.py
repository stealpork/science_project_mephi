import hashlib
from typing import Literal

from fastapi import Depends, Request, Response
from pydantic import Field, HttpUrl
from sqlalchemy import exists, or_, select, update

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
from libs.platform.schemas import Page, PaperDTO
from services.publications.models import Author, Paper, PaperAudit

app = create_app("publications")


class AuthorInput(Command):
    user_id: str | None = None
    display_name: str = Field(min_length=2, max_length=200)
    affiliation: str = Field(default="", max_length=300)
    position: int = Field(default=1, ge=1, le=100)


class PaperInput(Command):
    title: str = Field(min_length=3, max_length=300)
    abstract: str = Field(default="", max_length=20000)
    area_id: str | None = None
    university_id: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=30)
    year: int | None = Field(default=None, ge=1800, le=2100)
    external_links: list[HttpUrl] = Field(default_factory=list, max_length=20)
    project_id: str | None = None
    authors: list[AuthorInput] = Field(default_factory=list, max_length=100)


class PaperPatch(Command):
    non_nullable_fields = frozenset({"title", "abstract", "keywords", "external_links"})
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=3, max_length=300)
    abstract: str | None = Field(default=None, max_length=20000)
    area_id: str | None = None
    university_id: str | None = None
    keywords: list[str] | None = Field(default=None, max_length=30)
    year: int | None = Field(default=None, ge=1800, le=2100)
    external_links: list[HttpUrl] | None = Field(default=None, max_length=20)
    file_id: str | None = None
    project_id: str | None = None
    confirm_visibility_expansion: bool = False


async def is_author(db, paper_id, user_id):
    return bool(
        user_id
        and await db.scalar(
            select(Author.id).where(Author.paper_id == paper_id, Author.user_id == user_id)
        )
    )


async def get_paper(db, paper_id, request, user_id=None, edit=False, owner=False, lock=False):
    query = select(Paper).where(Paper.id == paper_id, Paper.moderation_status == "visible")
    row = await db.scalar(query.with_for_update() if lock else query)
    require(row is not None, "NOT_FOUND", "Работа не найдена", 404)
    author = await is_author(db, row.id, user_id)
    require(
        row.status == "published" or author or (user_id is not None and row.owner_id == user_id),
        "NOT_FOUND",
        "Работа не найдена",
        404,
    )
    if row.project_id:
        await internal_call(
            request,
            "projects",
            "POST",
            f"/internal/v1/projects/{row.project_id}/access",
            json={"actor_id": user_id, "action": "read"},
        )
    if edit:
        require(
            author and row.owner_id is not None and row.status != "archived",
            "FORBIDDEN",
            "Нет права редактирования",
            403,
        )
    if owner:
        require(
            user_id is not None and row.owner_id == user_id,
            "FORBIDDEN",
            "Требуются права владельца",
            403,
        )
    return row


def paper_card(row, authors, user_id=None):
    data = dto(row)
    data["authors"] = [
        {
            "author_id": a.id,
            "user_id": a.user_id,
            "display_name": a.display_name,
            "affiliation": a.affiliation,
            "position": a.position,
            "access_granted_at": a.access_granted_at.isoformat() if a.access_granted_at else None,
        }
        for a in authors
    ]
    data["viewer"] = {
        "can_edit": bool(
            row.status != "archived"
            and row.owner_id
            and user_id
            and any(a.user_id == user_id for a in authors)
        ),
        "is_owner": bool(user_id and row.owner_id == user_id),
        "can_manage_authors": bool(
            user_id and row.owner_id == user_id and row.status != "archived"
        ),
    }
    data["is_favorited"] = None
    return data


async def views(db, rows, user_id=None):
    grouped: dict[str, list[Author]] = {row.id: [] for row in rows}
    if grouped:
        authors = (
            await db.scalars(
                select(Author)
                .where(Author.paper_id.in_(grouped))
                .order_by(Author.position, Author.id)
            )
        ).all()
        for author in authors:
            grouped[author.paper_id].append(author)
    return [paper_card(row, grouped[row.id], user_id) for row in rows]


async def view(db, row, user_id=None):
    return (await views(db, [row], user_id))[0]


async def catalog_cards(db, query, result, user_id):
    identifiers = [item["id"] for item in result["items"]]
    rows = {row.id: row for row in (await db.scalars(query.where(Paper.id.in_(identifiers)))).all()}
    return await views(db, [rows[key] for key in identifiers if key in rows], user_id)


async def validate_metadata(request, values):
    ids = [values[key] for key in ("area_id", "university_id") if values.get(key)]
    if ids:
        references = await internal_call(
            request,
            "people",
            "POST",
            "/internal/v1/dictionaries/validate",
            json={"ids": list(set(ids))},
        )
        kinds = {item["id"]: item["kind"] for item in references["items"]}
        for field, kind in {"area_id": "research_areas", "university_id": "universities"}.items():
            if values.get(field):
                require(
                    kinds[values[field]] == kind,
                    "INVALID_DICTIONARY_KIND",
                    "Неверный тип значения справочника",
                    422,
                )


async def grant_author(request, db, paper, body, actor_id):
    if body.user_id:
        account = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{body.user_id}"
        )
        require(account["status"] == "active", "ACCOUNT_UNAVAILABLE", "Соавтор недоступен", 422)
    author = Author(
        id=uid(),
        paper_id=paper.id,
        **body.model_dump(),
        access_granted_at=now() if body.user_id else None,
    )
    db.add(author)
    db.add(
        PaperAudit(
            actor_id=actor_id,
            paper_id=paper.id,
            action="author_added",
            detail={"author_id": author.id, "user_id": body.user_id},
        )
    )
    return author


async def attach_file(request, db, paper, file_id, actor_id, old_file_id=None):
    info = await internal_call(
        request,
        "media",
        "POST",
        "/internal/v1/files/claim",
        json={
            "file_id": file_id,
            "resource_type": "paper",
            "resource_id": paper.id,
            "actor_id": actor_id,
        },
    )
    if paper.project_id:
        reservation = await internal_call(
            request,
            "projects",
            "POST",
            f"/internal/v1/projects/{paper.project_id}/reservations",
            json={
                "file_id": file_id,
                "actor_id": actor_id,
                "replaces_file_id": old_file_id,
                "paper_id": paper.id,
            },
        )
        await internal_call(
            request,
            "projects",
            "POST",
            f"/internal/v1/projects/{paper.project_id}/files",
            json={
                "file_id": file_id,
                "reservation_id": reservation["id"],
                "actor_id": actor_id,
                "paper_id": paper.id,
                "title": paper.title,
            },
        )
    paper.file_id = info["file_id"]


@app.post("/api/v1/papers", status_code=201, response_model=PaperDTO)
async def create_paper(
    body: PaperInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    require(
        not request.app.state.settings.email_verification_required or user["email_verified"],
        "EMAIL_NOT_VERIFIED",
        "Подтвердите email",
        403,
    )

    async def operation():
        values = body.model_dump(mode="json", exclude={"authors"})
        values["keywords"] = list(
            dict.fromkeys(
                value.strip().lower()[:100] for value in values["keywords"] if value.strip()
            )
        )
        await validate_metadata(request, values)
        if body.project_id:
            await internal_call(
                request,
                "projects",
                "POST",
                f"/internal/v1/projects/{body.project_id}/access",
                json={"actor_id": user["id"], "action": "link_paper"},
            )
        profile = await internal_call(
            request, "people", "GET", f"/internal/v1/profiles/{user['id']}"
        )
        paper = Paper(id=uid(), creator_id=user["id"], owner_id=user["id"], **values)
        db.add(paper)
        await db.flush()
        own = next((a for a in body.authors if a.user_id == user["id"]), None)
        await grant_author(
            request,
            db,
            paper,
            own
            or AuthorInput(
                user_id=user["id"], display_name=f"{profile['first_name']} {profile['last_name']}"
            ),
            user["id"],
        )
        for author in body.authors:
            if author.user_id != user["id"]:
                await grant_author(request, db, paper, author, user["id"])
        await db.flush()
        return await view(db, paper, user["id"])

    return await command(request, db, operation, 201)


@app.get("/api/v1/papers", response_model=Page[PaperDTO])
async def papers(
    request: Request,
    q: str = "",
    limit: int = 20,
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    query = select(Paper).where(Paper.status == "published", Paper.moderation_status == "visible")
    if q:
        query = query.where(or_(Paper.title.ilike(f"%{q}%"), Paper.abstract.ilike(f"%{q}%")))
    for key in ("area_id", "university_id"):
        values = request.query_params.getlist(key)
        if values:
            query = query.where(getattr(Paper, key).in_(values))
    if request.query_params.get("author_user_id"):
        query = query.where(
            exists(
                select(Author.id).where(
                    Author.paper_id == Paper.id,
                    Author.user_id == request.query_params["author_user_id"],
                )
            )
        )
    if request.query_params.get("author_name"):
        query = query.where(
            exists(
                select(Author.id).where(
                    Author.paper_id == Paper.id,
                    Author.display_name.ilike(f"%{request.query_params['author_name']}%"),
                )
            )
        )
    for key, operator in (("year_from", Paper.year.__ge__), ("year_to", Paper.year.__le__)):
        if request.query_params.get(key):
            try:
                query = query.where(operator(int(request.query_params[key])))
            except ValueError:
                raise APIError(422, "INVALID_YEAR", "Некорректный год") from None
    for keyword in request.query_params.getlist("keyword"):
        query = query.where(Paper.keywords.contains([keyword.lower()]))
    query = await readable_papers(db, query, request, user["id"] if user else None)
    result = await paginate(db, query, Paper, request, limit)
    result["items"] = await catalog_cards(db, query, result, user["id"] if user else None)
    await enrich_favorites(request, "paper", user, result["items"])
    return result


async def readable_papers(db, query, request, actor_id):
    project_ids = list(
        (
            await db.scalars(
                query.with_only_columns(Paper.project_id)
                .where(Paper.project_id.is_not(None))
                .distinct()
            )
        ).all()
    )
    readable: list[str] = []
    for offset in range(0, len(project_ids), 100):
        result = await internal_call(
            request,
            "projects",
            "POST",
            "/internal/v1/projects/readable",
            json={"actor_id": actor_id, "ids": project_ids[offset : offset + 100]},
        )
        readable.extend(item["id"] for item in result["items"])
    return query.where(or_(Paper.project_id.is_(None), Paper.project_id.in_(readable)))


@app.get("/api/v1/me/papers")
async def my_papers(
    request: Request,
    relation: str = "author",
    status: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    author = exists(
        select(Author.id).where(Author.paper_id == Paper.id, Author.user_id == user["id"])
    )
    query = select(Paper).where(
        Paper.owner_id == user["id"]
        if relation == "owner"
        else Paper.creator_id == user["id"]
        if relation == "creator"
        else author
    )
    query = query.where(
        Paper.moderation_status == "visible",
        or_(Paper.status == "published", Paper.owner_id == user["id"], author),
    )
    if status:
        query = query.where(Paper.status == status)
    query = await readable_papers(db, query, request, user["id"])
    result = await paginate(db, query, Paper, request, limit)
    result["items"] = await catalog_cards(db, query, result, user["id"])
    await enrich_favorites(request, "paper", user, result["items"])
    return result


@app.get("/api/v1/papers/{paper_id}", response_model=PaperDTO)
async def paper_detail(
    paper_id: str,
    request: Request,
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    row = await get_paper(db, paper_id, request, user["id"] if user else None)
    if row.status == "published":
        actor = (
            user["id"]
            if user
            else getattr(request.state, "client_ip", "unknown")
            + ":"
            + request.headers.get("User-Agent", "")[:300]
        )
        key = hashlib.sha256(
            (request.app.state.settings.service_token + actor).encode()
        ).hexdigest()
        try:
            counted = await request.app.state.redis.set(
                f"paper-view:{row.id}:{key}", "1", nx=True, ex=1800
            )
        except Exception:
            counted = False
        if counted:
            await db.execute(
                update(Paper)
                .where(Paper.id == row.id)
                .values(views_count=Paper.views_count + 1, updated_at=Paper.updated_at)
            )
            await db.refresh(row)
    cards = [await view(db, row, user["id"] if user else None)]
    return (await enrich_favorites(request, "paper", user, cards))[0]


@app.patch("/api/v1/papers/{paper_id}", response_model=PaperDTO)
async def edit_paper(
    paper_id: str,
    body: PaperPatch,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    row = await get_paper(db, paper_id, request, user["id"], edit=True, lock=True)
    assert_version(row, body.version)
    values = body.model_dump(
        mode="json",
        exclude_unset=True,
        exclude={"version", "file_id", "confirm_visibility_expansion"},
    )
    await validate_metadata(request, values)
    old_project = row.project_id
    if "project_id" in values and values["project_id"] != old_project:
        require(row.owner_id == user["id"], "FORBIDDEN", "Связь изменяет владелец", 403)
        for project_id in (old_project, values["project_id"]):
            if project_id:
                access = await internal_call(
                    request,
                    "projects",
                    "POST",
                    f"/internal/v1/projects/{project_id}/access",
                    json={"actor_id": user["id"], "action": "link_paper"},
                )
                if project_id == old_project and access["access_type"] == "private_invite":
                    require(
                        body.confirm_visibility_expansion,
                        "VISIBILITY_CONFIRMATION_REQUIRED",
                        "Подтвердите изменение доступа",
                    )
    for field, value in values.items():
        setattr(row, field, value)
    if body.file_id and body.file_id != row.file_id:
        await attach_file(
            request,
            db,
            row,
            body.file_id,
            user["id"],
            row.file_id if old_project == row.project_id else None,
        )
    elif old_project != row.project_id and row.project_id and row.file_id:
        await attach_file(request, db, row, row.file_id, user["id"])
    if "keywords" in values:
        row.keywords = list(
            dict.fromkeys(v.strip().lower()[:100] for v in row.keywords if v.strip())
        )
    await append_event(
        db,
        "publications.paper_changed",
        row.id,
        {"paper_id": row.id, "project_id": row.project_id, "file_id": row.file_id},
    )
    return await view(db, row, user["id"])


@app.post("/api/v1/papers/{paper_id}/publish")
async def publish_paper(
    paper_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        row = await get_paper(db, paper_id, request, user["id"], edit=True, lock=True)
        require(row.status == "draft", "INVALID_STATE", "Работа уже опубликована")
        require(
            bool(row.abstract and row.area_id and row.year and row.file_id),
            "PAPER_INCOMPLETE",
            "Заполните аннотацию, направление, год и PDF",
            422,
        )
        await internal_call(
            request,
            "media",
            "POST",
            "/internal/v1/files/claim",
            json={
                "file_id": row.file_id,
                "resource_type": "paper",
                "resource_id": row.id,
                "actor_id": user["id"],
            },
        )
        row.status, row.version = "published", row.version + 1
        await append_event(
            db,
            "publications.paper_published",
            row.id,
            {"paper_id": row.id, "owner_id": row.owner_id},
        )
        return await view(db, row, user["id"])

    return await command(request, db, operation)


@app.delete("/api/v1/papers/{paper_id}")
async def delete_paper(
    paper_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        row = await get_paper(db, paper_id, request, user["id"], owner=True, lock=True)
        row.status = "archived"
        await append_event(db, "publications.paper_changed", row.id, {"paper_id": row.id})
        return {"status": "archived"}

    return await command(request, db, operation)


async def download_url(db, paper_id, request, actor_id, mode):
    row = await get_paper(db, paper_id, request, actor_id)
    require(row.file_id is not None, "NOT_FOUND", "PDF ещё не загружен", 404)
    return await internal_call(
        request,
        "media",
        "POST",
        "/internal/v1/files/url",
        json={
            "file_id": row.file_id,
            "mode": mode,
            "resource_type": "paper",
            "resource_id": row.id,
        },
    )


@app.get("/api/v1/papers/{paper_id}/file-url")
async def download(
    paper_id: str,
    request: Request,
    response: Response,
    mode: Literal["inline", "attachment"] = "inline",
    user=Depends(optional_user),
    db=Depends(session, scope="function"),
):
    response.headers["Cache-Control"] = "no-store"
    return await download_url(db, paper_id, request, user["id"] if user else None, mode)


class AccessInput(Command):
    actor_id: str | None = None
    action: Literal["read", "upload"] = "read"


@app.post("/internal/v1/papers/{paper_id}/access", dependencies=[Depends(internal())])
async def access_check(
    paper_id: str, body: AccessInput, request: Request, db=Depends(session, scope="function")
):
    row = await get_paper(db, paper_id, request, body.actor_id, edit=body.action == "upload")
    return {
        "id": row.id,
        "project_id": row.project_id,
        "status": row.status,
        "file_id": row.file_id,
        "can_read": True,
    }


class DownloadInput(Command):
    actor_id: str | None = None
    mode: Literal["inline", "attachment"] = "inline"


@app.post(
    "/internal/v1/papers/{paper_id}/file-url",
    dependencies=[Depends(internal("projects", "activity"))],
)
async def internal_download(
    paper_id: str, body: DownloadInput, request: Request, db=Depends(session, scope="function")
):
    return await download_url(db, paper_id, request, body.actor_id, body.mode)


@app.post("/api/v1/papers/{paper_id}/authors", status_code=201)
async def add_author(
    paper_id: str,
    body: AuthorInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        row = await get_paper(db, paper_id, request, user["id"], owner=True, edit=True, lock=True)
        author = await grant_author(request, db, row, body, user["id"])
        await db.flush()
        return dto(author)

    return await command(request, db, operation, 201)


class AuthorPatch(Command):
    non_nullable_fields = frozenset({"display_name", "affiliation", "position"})
    display_name: str | None = Field(default=None, min_length=2, max_length=200)
    affiliation: str | None = Field(default=None, max_length=300)
    position: int | None = Field(default=None, ge=1, le=100)


async def author_owned(db, paper_id, author_id, request, user):
    paper = await get_paper(db, paper_id, request, user["id"], owner=True, edit=True, lock=True)
    row = await db.get(Author, author_id)
    require(row is not None and row.paper_id == paper_id, "NOT_FOUND", "Автор не найден", 404)
    return paper, row


@app.patch("/api/v1/papers/{paper_id}/authors/{author_id}")
async def edit_author(
    paper_id: str,
    author_id: str,
    body: AuthorPatch,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    _, row = await author_owned(db, paper_id, author_id, request, user)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    return dto(row)


class LinkAuthor(Command):
    user_id: str


@app.post("/api/v1/papers/{paper_id}/authors/{author_id}/link")
async def link_author(
    paper_id: str,
    author_id: str,
    body: LinkAuthor,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        _, row = await author_owned(db, paper_id, author_id, request, user)
        require(row.user_id is None, "AUTHOR_ALREADY_LINKED", "Автор уже связан с аккаунтом")
        target = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{body.user_id}"
        )
        require(target["status"] == "active", "ACCOUNT_UNAVAILABLE", "Аккаунт недоступен")
        row.user_id, row.access_granted_at = body.user_id, now()
        db.add(
            PaperAudit(
                actor_id=user["id"],
                paper_id=paper_id,
                action="author_linked",
                detail={"author_id": author_id, "user_id": body.user_id},
            )
        )
        return dto(row)

    return await command(request, db, operation)


@app.post("/api/v1/papers/{paper_id}/authors/{author_id}/unlink")
async def unlink_author(
    paper_id: str,
    author_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    paper, row = await author_owned(db, paper_id, author_id, request, user)
    require(row.user_id != paper.owner_id, "TRANSFER_REQUIRED", "Сначала передайте владение")
    row.user_id, row.access_granted_at = None, None
    db.add(
        PaperAudit(
            actor_id=user["id"],
            paper_id=paper_id,
            action="author_unlinked",
            detail={"author_id": author_id},
        )
    )
    return dto(row)


@app.delete("/api/v1/papers/{paper_id}/authors/{author_id}", status_code=204)
async def remove_author(
    paper_id: str,
    author_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    paper, row = await author_owned(db, paper_id, author_id, request, user)
    require(row.user_id != paper.owner_id, "TRANSFER_REQUIRED", "Сначала передайте владение")
    await db.delete(row)


@app.post("/api/v1/papers/{paper_id}/transfer-ownership")
async def transfer_owner(
    paper_id: str,
    body: LinkAuthor,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        paper = await get_paper(db, paper_id, request, user["id"], owner=True, edit=True, lock=True)
        require(
            await is_author(db, paper.id, body.user_id),
            "INVALID_AUTHOR",
            "Выберите зарегистрированного соавтора",
            422,
        )
        target = await internal_call(
            request, "identity", "GET", f"/internal/v1/users/{body.user_id}"
        )
        require(target["status"] == "active", "ACCOUNT_UNAVAILABLE", "Аккаунт недоступен")
        paper.owner_id = body.user_id
        return await view(db, paper, user["id"])

    return await command(request, db, operation)


from services.publications.extra import install as install_extra  # noqa: E402

install_extra(app, get_paper, view)
