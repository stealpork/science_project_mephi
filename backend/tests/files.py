import asyncio
import base64
import hashlib
import io

import httpx
from pypdf import PdfWriter

from tests.conftest import command, ok


def pdf_bytes(size=None):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    data = stream.getvalue()
    if size is not None:
        prefix, suffix = data.rsplit(b"startxref", 1)
        padding = size - len(data) - 2
        assert padding >= 0
        data = prefix + b"%" + b"x" * padding + b"\nstartxref" + suffix
        assert len(data) == size
    return data


async def upload_request(api, user, purpose, target, data, replaces=None):
    body = {
        "purpose": purpose,
        "resource_id": target,
        "filename": "Исследование.pdf",
        "content_type": "application/pdf",
        "size_bytes": len(data),
        "checksum_sha256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
    }
    if replaces:
        body["replaces_file_id"] = replaces
    return await command(api, user, "POST", "/api/v1/media/uploads", body)


async def uploaded_pdf(api, user, purpose, target, *, data=None, replaces=None):
    data = pdf_bytes() if data is None else data
    upload = ok(await upload_request(api, user, purpose, target, data, replaces), 201)
    async with httpx.AsyncClient(timeout=60) as s3:
        result = await s3.put(
            upload["upload_url"], content=data, headers=upload["required_headers"]
        )
        assert result.status_code == 200, result.text[:200]
    root = f"/api/v1/media/uploads/{upload['upload_id']}"
    ok(await command(api, user, "POST", root + "/complete"), 202)
    for _ in range(300):
        state = ok(await api.get(root, headers=user["headers"]))
        if state["status"] in {"ready", "rejected", "expired"}:
            break
        await asyncio.sleep(0.2)
    assert state["status"] == "ready", state
    return upload


async def attachment(api, user, project_id, upload):
    return ok(
        await command(
            api,
            user,
            "POST",
            f"/api/v1/projects/{project_id}/files",
            {
                "file_id": upload["file_id"],
                "reservation_id": upload["reservation_id"],
                "title": "Материал исследования",
            },
        ),
        201,
    )


async def publication(api, owner, *, project_id=None, authors=None):
    area = ok(await api.get("/api/v1/dictionaries/research_areas"))["items"][0]["id"]
    paper = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/papers",
            {
                "title": "Полный PDF workflow",
                "abstract": "Проверка материалов и видимости",
                "area_id": area,
                "year": 2026,
                "project_id": project_id,
                "authors": authors or [],
            },
        ),
        201,
    )
    upload = await uploaded_pdf(api, owner, "paper_pdf", paper["id"])
    paper = ok(
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=owner["headers"],
            json={"version": paper["version"], "file_id": upload["file_id"]},
        )
    )
    return ok(await command(api, owner, "POST", f"/api/v1/papers/{paper['id']}/publish"))
