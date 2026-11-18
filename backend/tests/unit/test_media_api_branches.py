import base64
from datetime import timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from starlette.responses import Response

from libs.platform.database import now, uid
from libs.platform.errors import APIError
from tests.unit.support import database, operation, request, service

pytestmark = pytest.mark.unit


@pytest.fixture
def media(monkeypatch):
    module = service(monkeypatch, "media")
    monkeypatch.setattr(module, "command", operation)
    monkeypatch.setattr(module, "append_event", AsyncMock())
    return module


def file(**changes):
    return Obj(
        **{
            "id": uid(),
            "user_id": uid(),
            "status": "created",
            "expires_at": now() + timedelta(days=1),
            "rejection_code": None,
            "filename": "paper.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
            "reservation_id": None,
            "quarantine_key": "quarantine",
            "purpose": "paper_pdf",
            "resource_id": uid(),
            **changes,
        }
    )


@pytest.mark.parametrize("checksum", ["not-base64!", base64.b64encode(b"short").decode()])
async def test_invalid_upload_checksum_is_rejected_before_any_write(media, checksum):
    db = database()
    body = media.UploadInput(
        purpose="paper_pdf",
        resource_id=uid(),
        filename="paper.pdf",
        content_type="application/pdf",
        size_bytes=100,
        checksum_sha256=checksum,
    )
    with pytest.raises(APIError) as error:
        await media.create_upload(body, request(), {"id": uid()}, db)
    assert error.value.code == "INVALID_CHECKSUM"
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "purpose,project",
    [("avatar", False), ("paper_pdf", False), ("paper_pdf", True), ("project_pdf", True)],
)
async def test_upload_permission_reserves_only_project_backed_files(
    media, monkeypatch, purpose, project
):
    req, db, identifier = request(), database(), uid()
    calls = AsyncMock(
        side_effect=[
            {"project_id": "project"} if project else {},
            {"id": "reservation", "expires_at": "future"},
        ]
    )
    monkeypatch.setattr(media, "internal_call", calls)
    signed = Obj(generate_presigned_url=lambda *args, **kwargs: "https://files.invalid/signed")
    monkeypatch.setattr(media, "s3_client", lambda *args: signed)
    body = media.UploadInput(
        purpose=purpose,
        resource_id=identifier,
        filename="file.pdf",
        content_type="image/png" if purpose == "avatar" else "application/pdf",
        size_bytes=100,
        checksum_sha256=base64.b64encode(b"x" * 32).decode(),
    )
    result = await media.create_upload(body, req, {"id": identifier}, db)
    assert result["reservation_id"] == ("reservation" if project else None)
    assert calls.await_count == (2 if project else 1)
    assert db.add.call_args.args[0].filename == "file.pdf"


@pytest.mark.parametrize("status", ["uploaded", "scanning", "ready", "rejected", "created"])
async def test_complete_is_idempotent_and_pins_object_version(media, monkeypatch, status):
    db, row = database(), file(status=status)
    db.scalar.return_value = row
    head = AsyncMock(return_value={"ContentLength": 100, "VersionId": "immutable-version"})
    monkeypatch.setattr(media, "s3_call", head)
    result = await media.complete(row.id, request(), {"id": row.user_id}, db)
    assert result["status"] == ("uploaded" if status == "created" else status)
    assert head.await_count == int(status == "created")
    assert media.append_event.await_count == int(status == "created")
    if status == "created":
        assert row.object_version == "immutable-version"


@pytest.mark.parametrize("status", ["created", "expired"])
async def test_cancel_expired_upload_does_not_emit_again(media, status):
    db, row = database(), file(status=status)
    db.scalar.side_effect = [row, None]
    await media.cancel_upload(row.id, {"id": row.user_id}, db)
    assert row.status == "expired" and media.append_event.await_count == int(status != "expired")


@pytest.mark.parametrize(
    "status,bound", [("scanning", False), ("rejected", False), ("ready", False), ("ready", True)]
)
async def test_claim_accepts_only_ready_files_and_deduplicates_binding(media, status, bound):
    db, row = database(), file(status=status)
    db.scalar.side_effect = [row, Obj(id="binding") if bound else None]
    body = media.ClaimInput(
        file_id=row.id, resource_id=row.resource_id, resource_type="paper", actor_id=row.user_id
    )
    if status == "ready":
        assert (await media.claim(body, "publications", db))["file_id"] == row.id
        assert db.add.call_count == int(not bound)
    else:
        with pytest.raises(APIError) as error:
            await media.claim(body, "publications", db)
        assert error.value.code == ("FILE_REJECTED" if status == "rejected" else "FILE_NOT_READY")
        db.add.assert_not_called()


async def test_download_has_no_store_and_scoped_signed_url(media, monkeypatch):
    db, row, response = database(), file(status="ready"), Response()
    db.get.return_value = row
    db.scalar.return_value = "binding"

    def signed(settings, target, mode):
        return f"https://files.invalid/{mode}/{target.id}"

    monkeypatch.setattr(media, "signed_download", signed)
    result = await media.file_url(
        media.DownloadInput(
            file_id=row.id, resource_id=row.resource_id, resource_type="paper", mode="attachment"
        ),
        request(),
        response,
        "activity",
        db,
    )
    assert result["url"].endswith("attachment/" + row.id)
    assert response.headers["Cache-Control"] == "no-store"
    info = await media.internal_file(row.id, db)
    assert info["resource_id"] == row.resource_id and info["user_id"] == row.user_id
    assert (await media.upload_status(row.id, {"id": row.user_id}, db))["file_id"] == row.id
