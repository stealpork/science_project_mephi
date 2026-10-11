import base64
from datetime import timedelta
from typing import Literal

from fastapi import Depends, Request, Response
from pydantic import Field
from sqlalchemy import select

from libs.platform.api import Command, command, create_app
from libs.platform.database import append_event, now, session, uid
from libs.platform.errors import APIError, require
from libs.platform.http import current_user, internal, internal_call
from libs.platform.schemas import UploadCreated, UploadState
from services.media.models import File, FileBinding
from services.media.storage import s3_call, s3_client, signed_download

app = create_app("media")


class UploadInput(Command):
    purpose: Literal["paper_pdf", "project_pdf", "avatar"]
    resource_id: str
    filename: str = Field(min_length=1, max_length=200)
    content_type: str
    size_bytes: int = Field(gt=0)
    checksum_sha256: str
    replaces_file_id: str | None = None


async def upload_permission(request, body, user):
    if body.purpose == "avatar":
        require(
            body.resource_id == user["id"], "FORBIDDEN", "Можно загрузить только свой аватар", 403
        )
        await internal_call(request, "people", "GET", f"/internal/v1/profiles/{user['id']}")
        return None
    service = "projects" if body.purpose == "project_pdf" else "publications"
    resource = "projects" if service == "projects" else "papers"
    return await internal_call(
        request,
        service,
        "POST",
        f"/internal/v1/{resource}/{body.resource_id}/access",
        json={"actor_id": user["id"], "action": "upload"},
    )


@app.post("/api/v1/media/uploads", status_code=201, response_model=UploadCreated)
async def create_upload(
    body: UploadInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    settings = request.app.state.settings
    require(
        not settings.email_verification_required or user["email_verified"],
        "EMAIL_NOT_VERIFIED",
        "Подтвердите email",
        403,
    )
    try:
        require(
            len(base64.b64decode(body.checksum_sha256, validate=True)) == 32,
            "INVALID_CHECKSUM",
            "Нужен SHA256 в base64",
            422,
        )
    except ValueError:
        raise APIError(422, "INVALID_CHECKSUM", "Нужен SHA256 в base64") from None
    allowed = (
        {"image/jpeg", "image/png", "image/webp"}
        if body.purpose == "avatar"
        else {"application/pdf"}
    )
    require(body.content_type in allowed, "INVALID_CONTENT_TYPE", "Недопустимый тип файла", 422)
    require(
        body.size_bytes
        <= (settings.max_avatar_bytes if body.purpose == "avatar" else settings.max_pdf_bytes),
        "FILE_TOO_LARGE",
        "Превышен размер файла",
        413,
    )

    async def operation():
        permission = await upload_permission(request, body, user)
        identifier = uid()
        reservation = None
        project_id = (
            body.resource_id
            if body.purpose == "project_pdf"
            else (permission or {}).get("project_id")
        )
        if project_id:
            reservation = await internal_call(
                request,
                "projects",
                "POST",
                f"/internal/v1/projects/{project_id}/reservations",
                json={
                    "actor_id": user["id"],
                    "file_id": identifier,
                    "replaces_file_id": body.replaces_file_id,
                    "paper_id": body.resource_id if body.purpose == "paper_pdf" else None,
                },
            )
        row = File(
            id=identifier,
            user_id=user["id"],
            purpose=body.purpose,
            resource_id=body.resource_id,
            filename=body.filename.replace("\r", "").replace("\n", ""),
            content_type=body.content_type,
            size_bytes=body.size_bytes,
            checksum_sha256=body.checksum_sha256,
            quarantine_key="uploads/" + uid(),
            expires_at=now() + timedelta(hours=24),
            reservation_id=reservation["id"] if reservation else None,
        )
        db.add(row)
        await db.flush()
        url = s3_client(settings, True).generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.s3_quarantine_bucket,
                "Key": row.quarantine_key,
                "ContentType": body.content_type,
                "ContentLength": body.size_bytes,
                "ChecksumSHA256": body.checksum_sha256,
            },
            ExpiresIn=600,
        )
        return {
            "upload_id": identifier,
            "file_id": identifier,
            "upload_method": "PUT",
            "upload_url": url,
            "required_headers": {
                "Content-Type": body.content_type,
                "x-amz-checksum-sha256": body.checksum_sha256,
            },
            "form_fields": {},
            "expires_at": (now() + timedelta(minutes=10)).isoformat(),
            "reservation_id": row.reservation_id,
            "reservation_expires_at": reservation["expires_at"] if reservation else None,
        }

    return await command(request, db, operation, 201)


@app.post("/api/v1/media/uploads/{upload_id}/complete", status_code=202, response_model=UploadState)
async def complete(
    upload_id: str,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        row = await db.scalar(select(File).where(File.id == upload_id).with_for_update())
        require(
            row is not None and row.user_id == user["id"], "NOT_FOUND", "Загрузка не найдена", 404
        )
        if row.status in {"uploaded", "scanning", "ready", "rejected"}:
            return file_view(row)
        require(
            row.status == "created" and row.expires_at > now(), "UPLOAD_EXPIRED", "Загрузка истекла"
        )
        head = await s3_call(
            request.app.state.settings,
            "head_object",
            Bucket=request.app.state.settings.s3_quarantine_bucket,
            Key=row.quarantine_key,
        )
        require(
            head["ContentLength"] == row.size_bytes and bool(head.get("VersionId")),
            "INVALID_UPLOAD",
            "Размер или версия файла не подтверждены",
            422,
        )
        row.object_version, row.status = head["VersionId"], "uploaded"
        await append_event(db, "media.upload_completed", row.id, {"file_id": row.id})
        return file_view(row)

    return await command(request, db, operation, 202)


def file_view(row):
    return {
        "upload_id": row.id,
        "file_id": row.id,
        "status": row.status,
        "rejection_code": row.rejection_code,
        "expires_at": row.expires_at.isoformat(),
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "reservation_id": row.reservation_id,
    }


@app.get("/api/v1/media/uploads/{upload_id}", response_model=UploadState)
async def upload_status(
    upload_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    row = await db.get(File, upload_id)
    require(row is not None and row.user_id == user["id"], "NOT_FOUND", "Загрузка не найдена", 404)
    return file_view(row)


@app.delete("/api/v1/media/uploads/{upload_id}", status_code=204)
async def cancel_upload(
    upload_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    row = await db.scalar(select(File).where(File.id == upload_id).with_for_update())
    require(row is not None and row.user_id == user["id"], "NOT_FOUND", "Загрузка не найдена", 404)
    binding = await db.scalar(select(FileBinding.id).where(FileBinding.file_id == upload_id))
    require(not binding, "FILE_IN_USE", "Файл уже используется")
    if row.status != "expired":
        await append_event(
            db,
            "media.upload_cancelled",
            row.id,
            {"file_id": row.id, "reservation_id": row.reservation_id},
        )
    row.status = "expired"


class ClaimInput(Command):
    file_id: str
    resource_type: Literal["profile", "paper", "project"]
    resource_id: str
    actor_id: str


@app.post("/internal/v1/files/claim")
async def claim(
    body: ClaimInput,
    caller=Depends(internal("people", "projects", "publications")),
    db=Depends(session, scope="function"),
):
    require(
        caller
        == {"profile": "people", "paper": "publications", "project": "projects"}[
            body.resource_type
        ],
        "SERVICE_FORBIDDEN",
        "Нет права привязки",
        403,
    )
    row = await db.scalar(select(File).where(File.id == body.file_id).with_for_update())
    require(row is not None, "NOT_FOUND", "Файл не найден", 404)
    require(
        row.status == "ready",
        "FILE_NOT_READY" if row.status != "rejected" else "FILE_REJECTED",
        "Файл не готов к использованию",
    )
    require(
        row.purpose
        == {"profile": "avatar", "paper": "paper_pdf", "project": "project_pdf"}[body.resource_type]
        and row.resource_id == body.resource_id,
        "FILE_TARGET_MISMATCH",
        "Файл предназначен для другой сущности",
        403,
    )
    binding = await db.scalar(
        select(FileBinding).where(
            FileBinding.file_id == row.id,
            FileBinding.resource_type == body.resource_type,
            FileBinding.resource_id == body.resource_id,
        )
    )
    if not binding:
        db.add(
            FileBinding(
                file_id=row.id, resource_type=body.resource_type, resource_id=body.resource_id
            )
        )
    return file_view(row)


class DownloadInput(Command):
    file_id: str
    mode: Literal["inline", "attachment"] = "inline"
    resource_type: Literal["profile", "paper", "project"]
    resource_id: str


@app.post("/internal/v1/files/url")
async def file_url(
    body: DownloadInput,
    request: Request,
    response: Response,
    caller=Depends(internal("people", "projects", "publications", "activity")),
    db=Depends(session, scope="function"),
):
    require(
        caller
        in {
            "activity",
            {"profile": "people", "paper": "publications", "project": "projects"}[
                body.resource_type
            ],
        },
        "SERVICE_FORBIDDEN",
        "Нет доступа",
        403,
    )
    row = await db.get(File, body.file_id)
    binding = await db.scalar(
        select(FileBinding.id).where(
            FileBinding.file_id == body.file_id,
            FileBinding.resource_type == body.resource_type,
            FileBinding.resource_id == body.resource_id,
        )
    )
    require(
        row is not None and row.status == "ready" and binding is not None,
        "NOT_FOUND",
        "Файл не найден",
        404,
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "url": signed_download(request.app.state.settings, row, body.mode),
        "expires_at": (now() + timedelta(minutes=5)).isoformat(),
        "filename": row.filename,
        "content_type": row.content_type,
    }


@app.get("/internal/v1/files/{file_id}", dependencies=[Depends(internal())])
async def internal_file(file_id: str, db=Depends(session, scope="function")):
    row = await db.get(File, file_id)
    require(row is not None, "NOT_FOUND", "Файл не найден", 404)
    return dict(
        file_view(row), resource_id=row.resource_id, purpose=row.purpose, user_id=row.user_id
    )
