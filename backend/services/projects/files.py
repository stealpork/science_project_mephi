from datetime import timedelta

from fastapi import Depends, Request, Response
from pydantic import Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from libs.platform.api import Command, command, dto
from libs.platform.database import now, session, uid
from libs.platform.errors import require
from libs.platform.http import current_user, internal, internal_call, optional_user
from services.projects.models import FileReservation, ProjectFile, ProjectSetting


def install(app, get_project, membership):
    async def policy(db):
        await db.execute(
            insert(ProjectSetting)
            .values(id=uid(), name="max_files_per_project", value=5)
            .on_conflict_do_nothing(index_elements=["name"])
        )
        return await db.scalar(
            select(ProjectSetting).where(ProjectSetting.name == "max_files_per_project")
        )

    async def count_files(db, project_id):
        used = (
            await db.scalar(
                select(func.count())
                .select_from(ProjectFile)
                .where(ProjectFile.project_id == project_id, ProjectFile.active.is_(True))
            )
            or 0
        )
        reserved = (
            await db.scalar(
                select(func.count())
                .select_from(FileReservation)
                .where(
                    FileReservation.project_id == project_id,
                    FileReservation.status == "pending",
                    FileReservation.expires_at > now(),
                    FileReservation.replaces_file_id.is_(None),
                )
            )
            or 0
        )
        return used, reserved

    async def replacement_target(db, project, file_id, actor_id, paper_id):
        old = await db.scalar(
            select(ProjectFile).where(
                ProjectFile.project_id == project.id,
                ProjectFile.file_id == file_id,
                ProjectFile.active.is_(True),
            )
        )
        require(old is not None, "REPLACEMENT_TARGET_CHANGED", "Заменяемый файл уже недоступен")
        if old.origin == "paper":
            require(
                bool(paper_id) and old.paper_id == paper_id,
                "PAPER_FILE",
                "PDF работы изменяется только через соответствующую работу",
            )
        else:
            require(
                paper_id is None and actor_id in {old.uploaded_by, project.owner_id},
                "FORBIDDEN",
                "Нет права заменять чужой файл",
                403,
            )
        return old

    async def reserve(project_id, body, request, db):
        project = await get_project(db, project_id, {"id": body.actor_id}, True)
        require(
            project.owner_id is not None and project.status != "archived",
            "READ_ONLY",
            "Проект доступен только для чтения",
            403,
        )
        member = await membership(db, project_id, body.actor_id)
        if not member and body.paper_id:
            access = await internal_call(
                request,
                "publications",
                "POST",
                f"/internal/v1/papers/{body.paper_id}/access",
                json={"actor_id": body.actor_id, "action": "upload"},
            )
            require(
                access.get("project_id") == project_id,
                "FILE_TARGET_MISMATCH",
                "Работа не относится к проекту",
                403,
            )
        else:
            require(member is not None, "FORBIDDEN", "Нет права загрузки", 403)
        await db.execute(
            update(FileReservation)
            .where(
                FileReservation.project_id == project_id,
                FileReservation.status == "pending",
                FileReservation.expires_at <= now(),
            )
            .values(status="expired")
        )
        existing = await db.scalar(
            select(FileReservation).where(
                FileReservation.project_id == project_id,
                FileReservation.file_id == body.file_id,
                FileReservation.status == "pending",
            )
        )
        if existing:
            require(existing.actor_id == body.actor_id, "FORBIDDEN", "Нет доступа к резерву", 403)
            return dto(existing)
        if body.replaces_file_id:
            await replacement_target(
                db, project, body.replaces_file_id, body.actor_id, body.paper_id
            )
            replacing = await db.scalar(
                select(FileReservation.id).where(
                    FileReservation.project_id == project_id,
                    FileReservation.replaces_file_id == body.replaces_file_id,
                    FileReservation.status == "pending",
                )
            )
            require(not replacing, "REPLACEMENT_PENDING", "Файл уже заменяется")
        else:
            used, pending = await count_files(db, project_id)
            limit = (await policy(db)).value
            require(
                used + pending < limit,
                "PROJECT_FILE_LIMIT_REACHED",
                "Достигнут лимит файлов проекта",
            )
        row = FileReservation(
            id=uid(),
            project_id=project_id,
            file_id=body.file_id,
            actor_id=body.actor_id,
            replaces_file_id=body.replaces_file_id,
            expires_at=now() + timedelta(hours=24),
        )
        db.add(row)
        await db.flush()
        return dto(row)

    async def attach(project_id, body, request, db, paper_id=None):
        project = await get_project(db, project_id, {"id": body.actor_id}, True)
        require(
            project.owner_id is not None and project.status != "archived",
            "READ_ONLY",
            "Проект доступен только для чтения",
            403,
        )
        existing = await db.scalar(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id, ProjectFile.file_id == body.file_id
            )
        )
        if existing and existing.active:
            return dto(existing)
        reservation = await db.scalar(
            select(FileReservation).where(
                FileReservation.id == body.reservation_id,
                FileReservation.project_id == project_id,
                FileReservation.file_id == body.file_id,
                FileReservation.actor_id == body.actor_id,
            )
        )
        require(
            reservation is not None
            and reservation.status == "pending"
            and reservation.expires_at > now(),
            "FILE_RESERVATION_EXPIRED",
            "Резерв истёк, запросите место заново",
        )
        if reservation.replaces_file_id:
            await replacement_target(
                db, project, reservation.replaces_file_id, body.actor_id, paper_id
            )
        settings = await policy(db)
        used, _ = await count_files(db, project_id)
        require(
            bool(reservation.replaces_file_id) or used < settings.value,
            "PROJECT_FILE_LIMIT_REACHED",
            "Достигнут лимит файлов проекта",
        )
        if paper_id:
            info = await internal_call(
                request, "media", "GET", f"/internal/v1/files/{body.file_id}"
            )
            require(
                info["status"] == "ready"
                and info["purpose"] == "paper_pdf"
                and info["resource_id"] == paper_id,
                "FILE_TARGET_MISMATCH",
                "Неверное назначение файла",
                403,
            )
        else:
            await internal_call(
                request,
                "media",
                "POST",
                "/internal/v1/files/claim",
                json={
                    "file_id": body.file_id,
                    "resource_type": "project",
                    "resource_id": project_id,
                    "actor_id": body.actor_id,
                },
            )
        if reservation.replaces_file_id:
            await db.execute(
                update(ProjectFile)
                .where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.file_id == reservation.replaces_file_id,
                )
                .values(active=False)
            )
        reservation.status = "fulfilled"
        if existing:
            existing.active, existing.paper_id, existing.origin = (
                True,
                paper_id,
                "paper" if paper_id else "attachment",
            )
            row = existing
        else:
            row = ProjectFile(
                id=uid(),
                project_id=project_id,
                file_id=body.file_id,
                uploaded_by=body.actor_id,
                title=body.title,
                origin="paper" if paper_id else "attachment",
                paper_id=paper_id,
            )
            db.add(row)
        await db.flush()
        return dto(row)

    class ReservationInput(Command):
        actor_id: str
        file_id: str
        replaces_file_id: str | None = None
        paper_id: str | None = None

    class PublicReserve(Command):
        file_id: str
        replaces_file_id: str | None = None

    class AttachInput(Command):
        file_id: str
        reservation_id: str
        title: str = Field(default="", max_length=200)

    class InternalAttach(AttachInput):
        actor_id: str
        paper_id: str | None = None

    @app.post(
        "/internal/v1/projects/{project_id}/reservations",
        dependencies=[Depends(internal("media", "publications"))],
    )
    async def internal_reserve(
        project_id: str,
        body: ReservationInput,
        request: Request,
        db=Depends(session, scope="function"),
    ):
        return await reserve(project_id, body, request, db)

    @app.post("/api/v1/projects/{project_id}/file-reservations", status_code=201)
    async def public_reserve(
        project_id: str,
        body: PublicReserve,
        request: Request,
        user=Depends(current_user),
        db=Depends(session, scope="function"),
    ):
        info = await internal_call(request, "media", "GET", f"/internal/v1/files/{body.file_id}")
        require(
            info["status"] == "ready"
            and info["resource_id"] == project_id
            and info["purpose"] == "project_pdf",
            "FILE_TARGET_MISMATCH",
            "Неверное назначение файла",
            403,
        )
        return await reserve(
            project_id, ReservationInput(**body.model_dump(), actor_id=user["id"]), request, db
        )

    @app.post("/api/v1/projects/{project_id}/files", status_code=201)
    async def add_file(
        project_id: str,
        body: AttachInput,
        request: Request,
        user=Depends(current_user),
        db=Depends(session, scope="function"),
    ):
        async def operation():
            require(
                await membership(db, project_id, user["id"]) is not None,
                "FORBIDDEN",
                "Нет прав",
                403,
            )
            return await attach(
                project_id, InternalAttach(**body.model_dump(), actor_id=user["id"]), request, db
            )

        return await command(request, db, operation, 201)

    @app.post(
        "/internal/v1/projects/{project_id}/files", dependencies=[Depends(internal("publications"))]
    )
    async def internal_attach(
        project_id: str,
        body: InternalAttach,
        request: Request,
        db=Depends(session, scope="function"),
    ):
        require(bool(body.paper_id), "INVALID_PAPER", "Нужна работа", 422)
        return await attach(project_id, body, request, db, body.paper_id)

    @app.get("/api/v1/projects/{project_id}/files")
    async def files(
        project_id: str,
        request: Request,
        user=Depends(optional_user),
        db=Depends(session, scope="function"),
    ):
        project = await get_project(db, project_id, user)
        rows = (
            await db.scalars(
                select(ProjectFile).where(
                    ProjectFile.project_id == project_id, ProjectFile.active.is_(True)
                )
            )
        ).all()
        items = []
        for row in rows:
            if row.paper_id:
                try:
                    await internal_call(
                        request,
                        "publications",
                        "POST",
                        f"/internal/v1/papers/{row.paper_id}/access",
                        json={"actor_id": user["id"] if user else None, "action": "read"},
                    )
                except Exception as exc:
                    from libs.platform.errors import APIError

                    if isinstance(exc, APIError) and exc.status in {403, 404}:
                        continue
                    raise
            items.append(dto(row))
        used, reserved = await count_files(db, project_id)
        limit = (await policy(db)).value
        return {
            "items": items,
            "limit": limit,
            "used_slots": used,
            "reserved_slots": reserved,
            "can_upload": bool(
                user
                and project.owner_id is not None
                and project.status != "archived"
                and await membership(db, project_id, user["id"])
                and used + reserved < limit
            ),
        }

    @app.delete("/api/v1/projects/{project_id}/files/{file_id}", status_code=204)
    async def delete_file(
        project_id: str,
        file_id: str,
        user=Depends(current_user),
        db=Depends(session, scope="function"),
    ):
        project = await get_project(db, project_id, user, True)
        require(
            project.owner_id is not None and project.status != "archived",
            "READ_ONLY",
            "Проект доступен только для чтения",
            403,
        )
        row = await db.scalar(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.file_id == file_id,
                ProjectFile.active.is_(True),
            )
        )
        require(row is not None, "NOT_FOUND", "Файл не найден", 404)
        require(row.origin == "attachment", "PAPER_FILE", "PDF работы изменяется через работу")
        require(
            user["id"] in {row.uploaded_by, project.owner_id},
            "FORBIDDEN",
            "Нет права удалить файл",
            403,
        )
        row.active = False

    @app.get("/api/v1/projects/{project_id}/files/{file_id}/file-url")
    async def download(
        project_id: str,
        file_id: str,
        request: Request,
        response: Response,
        mode: str = "inline",
        user=Depends(optional_user),
        db=Depends(session, scope="function"),
    ):
        await get_project(db, project_id, user)
        row = await db.scalar(
            select(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.file_id == file_id,
                ProjectFile.active.is_(True),
            )
        )
        require(row is not None, "NOT_FOUND", "Файл не найден", 404)
        require(mode in {"inline", "attachment"}, "INVALID_MODE", "Неверный режим", 422)
        if row.paper_id:
            await internal_call(
                request,
                "publications",
                "POST",
                f"/internal/v1/papers/{row.paper_id}/access",
                json={"actor_id": user["id"] if user else None, "action": "read"},
            )
            result = await internal_call(
                request,
                "publications",
                "POST",
                f"/internal/v1/papers/{row.paper_id}/file-url",
                json={"actor_id": user["id"] if user else None, "mode": mode},
            )
        else:
            result = await internal_call(
                request,
                "media",
                "POST",
                "/internal/v1/files/url",
                json={
                    "file_id": file_id,
                    "resource_type": "project",
                    "resource_id": project_id,
                    "mode": mode,
                },
            )
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.get("/internal/v1/settings/project-files", dependencies=[Depends(internal("activity"))])
    async def get_policy(db=Depends(session, scope="function")):
        row = await policy(db)
        return {"max_files_per_project": row.value, "version": row.version}

    class PolicyPatch(Command):
        max_files_per_project: int = Field(ge=1, le=1000)
        version: int = Field(ge=1)

    @app.patch("/internal/v1/settings/project-files", dependencies=[Depends(internal("activity"))])
    async def edit_policy(body: PolicyPatch, db=Depends(session, scope="function")):
        from libs.platform.api import assert_version

        row = await policy(db)
        await db.refresh(row, with_for_update=True)
        assert_version(row, body.version)
        row.value = body.max_files_per_project
        return {"max_files_per_project": row.value, "version": row.version}
