import asyncio
import hashlib
from uuid import uuid4

import httpx
import pytest

from tests.conftest import command, ok, project
from tests.files import attachment, pdf_bytes, publication, upload_request, uploaded_pdf

pytestmark = pytest.mark.e2e


async def join(api, owner, member, project_id):
    root = f"/api/v1/projects/{project_id}"
    invitation = ok(
        await command(api, owner, "POST", root + "/invitations", {"user_id": member["id"]}), 201
    )
    ok(await command(api, member, "POST", root + f"/invitations/{invitation['id']}/accept", {}))


async def test_attachment_download_replace_delete_and_permissions(api, users):
    owner, uploader, other = users
    row = await project(api, owner)
    root = f"/api/v1/projects/{row['id']}"
    await join(api, owner, uploader, row["id"])
    await join(api, owner, other, row["id"])
    original = await uploaded_pdf(api, uploader, "project_pdf", row["id"])
    await attachment(api, uploader, row["id"], original)
    await attachment(api, uploader, row["id"], original)
    assert ok(await api.get(root + "/files"))["used_slots"] == 1
    original_path = root + f"/files/{original['file_id']}"
    assert (await api.delete(original_path, headers=other["headers"])).status_code == 403
    denied = await upload_request(
        api, other, "project_pdf", row["id"], pdf_bytes(), original["file_id"]
    )
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "FORBIDDEN"
    assert (await api.get(original_path + "/file-url?mode=wrong")).status_code == 422
    response = await api.get(original_path + "/file-url?mode=attachment")
    assert "no-store" in response.headers["Cache-Control"]
    async with httpx.AsyncClient() as s3:
        result = await s3.get(ok(response)["url"])
    assert result.status_code == 200 and result.content == pdf_bytes()
    assert "attachment" in result.headers["Content-Disposition"]
    replacement = await uploaded_pdf(
        api, owner, "project_pdf", row["id"], replaces=original["file_id"]
    )
    duplicate = await upload_request(
        api, owner, "project_pdf", row["id"], pdf_bytes(), original["file_id"]
    )
    assert duplicate.status_code == 409
    await attachment(api, owner, row["id"], replacement)
    assert (await api.get(original_path + "/file-url")).status_code == 404
    files = ok(await api.get(root + "/files"))
    assert files["used_slots"] == 1 and files["reserved_slots"] == 0
    assert [item["file_id"] for item in files["items"]] == [replacement["file_id"]]
    assert (
        await api.delete(
            f"/api/v1/media/uploads/{replacement['upload_id']}", headers=owner["headers"]
        )
    ).status_code == 409
    ok(await api.delete(root + f"/files/{replacement['file_id']}", headers=owner["headers"]), 204)
    assert ok(await api.get(root + "/files"))["used_slots"] == 0


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_deleted_replacement_target_cannot_bypass_quota(api, users):
    owner = users[0]
    row = await project(api, owner)
    root = f"/api/v1/projects/{row['id']}"
    original = await uploaded_pdf(api, owner, "project_pdf", row["id"])
    await attachment(api, owner, row["id"], original)
    replacement = await uploaded_pdf(
        api, owner, "project_pdf", row["id"], replaces=original["file_id"]
    )
    ok(await api.delete(root + f"/files/{original['file_id']}", headers=owner["headers"]), 204)
    response = await command(
        api,
        owner,
        "POST",
        root + "/files",
        {
            "file_id": replacement["file_id"],
            "reservation_id": replacement["reservation_id"],
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REPLACEMENT_TARGET_CHANGED"
    assert ok(await api.get(root + "/files"))["used_slots"] == 0
    ok(
        await api.delete(
            f"/api/v1/media/uploads/{replacement['upload_id']}", headers=owner["headers"]
        ),
        204,
    )


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_linked_paper_cannot_be_overwritten_as_attachment(api, users):
    owner = users[0]
    row = await project(api, owner)
    paper = await publication(api, owner, project_id=row["id"])
    root = f"/api/v1/projects/{row['id']}"
    response = await upload_request(
        api, owner, "project_pdf", row["id"], pdf_bytes(), paper["file_id"]
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "PAPER_FILE"
    response = await api.delete(root + f"/files/{paper['file_id']}", headers=owner["headers"])
    assert response.status_code == 409
    replacement = await uploaded_pdf(
        api, owner, "paper_pdf", paper["id"], replaces=paper["file_id"]
    )
    updated = ok(
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=owner["headers"],
            json={
                "version": paper["version"],
                "file_id": replacement["file_id"],
            },
        )
    )
    assert updated["file_id"] == replacement["file_id"]
    assert ok(await api.get(root + "/files"))["used_slots"] == 1


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_published_pdf_moves_between_projects_with_explicit_visibility_consent(api, users):
    owner = users[0]
    private = await project(api, owner, access="private_invite")
    public = await project(api, owner)
    paper = await publication(api, owner, project_id=private["id"])
    root = f"/api/v1/papers/{paper['id']}"
    assert (await api.get(root)).status_code == 404
    rejected = await api.patch(
        root,
        headers=owner["headers"],
        json={
            "version": paper["version"],
            "project_id": public["id"],
        },
    )
    assert (
        rejected.status_code == 409
        and rejected.json()["error"]["code"] == "VISIBILITY_CONFIRMATION_REQUIRED"
    )
    moved = ok(
        await api.patch(
            root,
            headers=owner["headers"],
            json={
                "version": paper["version"],
                "project_id": public["id"],
                "confirm_visibility_expansion": True,
            },
        )
    )
    assert moved["project_id"] == public["id"] and (await api.get(root)).status_code == 200
    for _ in range(100):
        old = ok(await api.get(f"/api/v1/projects/{private['id']}/files", headers=owner["headers"]))
        if old["used_slots"] == 0:
            break
        await asyncio.sleep(0.1)
    assert old["used_slots"] == 0
    new = ok(await api.get(f"/api/v1/projects/{public['id']}/files"))
    assert new["used_slots"] == 1 and new["items"][0]["paper_id"] == paper["id"]
    url = ok(await api.get(f"/api/v1/projects/{public['id']}/files/{paper['file_id']}/file-url"))
    async with httpx.AsyncClient() as s3:
        assert (await s3.get(url["url"])).content == pdf_bytes()
    ok(
        await api.patch(
            root, headers=owner["headers"], json={"version": moved["version"], "project_id": None}
        )
    )
    assert (await api.get(root)).status_code == 200


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_exact_25mb_pdf_is_scanned_stored_and_downloaded(api, users):
    owner = users[0]
    row = await project(api, owner)
    data = pdf_bytes(25_000_000)
    upload = await uploaded_pdf(api, owner, "project_pdf", row["id"], data=data)
    await attachment(api, owner, row["id"], upload)
    url = ok(await api.get(f"/api/v1/projects/{row['id']}/files/{upload['file_id']}/file-url"))
    async with httpx.AsyncClient(timeout=60) as s3:
        downloaded = await s3.get(url["url"])
    assert len(downloaded.content) == 25_000_000
    assert hashlib.sha256(downloaded.content).digest() == hashlib.sha256(data).digest()
    too_big = await upload_request(api, owner, "project_pdf", row["id"], data + b"x")
    assert too_big.status_code == 413


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_s3_rejects_changed_signed_size_or_checksum(api, users):
    owner = users[0]
    row = await project(api, owner)
    data = pdf_bytes()
    upload = ok(await upload_request(api, owner, "project_pdf", row["id"], data), 201)
    async with httpx.AsyncClient() as s3:
        for payload in [data + b"extra", b"x" * len(data)]:
            result = await s3.put(
                upload["upload_url"], content=payload, headers=upload["required_headers"]
            )
            assert result.status_code in {400, 403}
    ok(
        await api.delete(f"/api/v1/media/uploads/{upload['upload_id']}", headers=owner["headers"]),
        204,
    )
    assert (
        await command(api, owner, "POST", f"/api/v1/media/uploads/{upload['upload_id']}/complete")
    ).status_code == 409
    assert (
        await api.get(f"/api/v1/projects/{row['id']}/files/{uuid4()}/file-url")
    ).status_code == 404
