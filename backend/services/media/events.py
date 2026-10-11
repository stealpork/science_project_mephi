import asyncio
import base64
import hashlib
import json
import struct
import sys

from sqlalchemy import select

from libs.platform.database import append_event, now
from services.media.models import File
from services.media.storage import s3_call

BINDINGS = ["media.upload_completed"]


async def delete_user(request, db, user_id):
    from sqlalchemy import String, cast, exists, or_, update

    from services.media.models import FileBinding

    bound = exists(select(FileBinding.id).where(FileBinding.file_id == cast(File.id, String)))
    await db.execute(
        update(File)
        .where(File.user_id == user_id, or_(File.purpose == "avatar", ~bound))
        .values(status="expired")
    )


async def antivirus(settings, data):
    if not settings.scanner_required:
        if settings.environment == "production":
            raise RuntimeError("Scanner cannot be disabled in production")
        return
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(settings.scanner_host, settings.scanner_port), timeout=5
    )
    try:
        writer.write(b"zINSTREAM\0")
        for start in range(0, len(data), 65536):
            chunk = data[start : start + 65536]
            writer.write(struct.pack("!I", len(chunk)) + chunk)
            await writer.drain()
        writer.write(b"\0\0\0\0")
        await writer.drain()
        result = await asyncio.wait_for(reader.read(2048), timeout=60)
        if b"FOUND" in result:
            raise ValueError("MALWARE_FOUND")
        if b"OK" not in result:
            raise RuntimeError("Invalid scanner response")
    finally:
        writer.close()
        await writer.wait_closed()


async def handle(request, db, event):
    row = await db.scalar(
        select(File).where(File.id == event["payload"]["file_id"]).with_for_update()
    )
    if not row or row.status not in {"uploaded", "scanning"}:
        return
    settings = request.app.state.settings
    row.status = "scanning"
    result = await s3_call(
        settings,
        "get_object",
        Bucket=settings.s3_quarantine_bucket,
        Key=row.quarantine_key,
        VersionId=row.object_version,
    )

    def read_bytes():
        try:
            return result["Body"].read(settings.max_pdf_bytes + 1)
        finally:
            result["Body"].close()

    data = await asyncio.to_thread(read_bytes)
    try:
        if (
            len(data) != row.size_bytes
            or base64.b64encode(hashlib.sha256(data).digest()).decode() != row.checksum_sha256
        ):
            raise ValueError("CHECKSUM_MISMATCH")
        await antivirus(settings, data)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "services.media.validate",
            "image" if row.purpose == "avatar" else "pdf",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(data), timeout=25)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ValueError("FILE_PROCESSING_TIMEOUT") from None
        validation = json.loads(output)
        if not validation.get("valid"):
            raise ValueError(validation.get("code", "INVALID_FILE"))
        if row.purpose == "avatar":
            data = base64.b64decode(validation["image"])
            row.content_type = "image/png"
            row.filename = "avatar.png"
        key = f"files/{row.id}.{'png' if row.purpose == 'avatar' else 'pdf'}"
        await s3_call(
            settings,
            "put_object",
            Bucket=settings.s3_files_bucket,
            Key=key,
            Body=data,
            ContentType=row.content_type,
        )
        row.final_key, row.status = key, "ready"
        await append_event(
            db, "media.file_ready", row.id, {"file_id": row.id, "user_id": row.user_id}
        )
    except ValueError as exc:
        row.status, row.rejection_code = "rejected", str(exc)[:100]
        await append_event(
            db,
            "media.file_rejected",
            row.id,
            {
                "file_id": row.id,
                "user_id": row.user_id,
                "code": row.rejection_code,
                "reservation_id": row.reservation_id,
            },
        )


async def reconcile(request, db):
    expired = (
        await db.scalars(
            select(File)
            .where(File.status.in_(["created", "uploaded", "scanning"]), File.expires_at <= now())
            .with_for_update(skip_locked=True)
            .limit(100)
        )
    ).all()
    for row in expired:
        row.status = "expired"
        await append_event(
            db,
            "media.upload_expired",
            row.id,
            {"file_id": row.id, "reservation_id": row.reservation_id},
        )
    rows = (
        await db.scalars(
            select(File)
            .where(File.status.in_(["uploaded", "scanning"]), File.expires_at > now())
            .with_for_update(skip_locked=True)
            .limit(50)
        )
    ).all()
    for row in rows:
        await append_event(db, "media.upload_completed", row.id, {"file_id": row.id})
