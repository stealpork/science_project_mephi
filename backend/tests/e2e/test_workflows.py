import asyncio
import base64
import hashlib
import io
from uuid import uuid4

import httpx
import pytest
from pypdf import PdfWriter

from tests.conftest import command, ok, project

pytestmark = pytest.mark.e2e


async def test_auth_profile_contacts_and_refresh_replay(api, users):
    owner, other, _ = users
    profile = ok(await api.get("/api/v1/me/profile", headers=owner["headers"]))
    updated = ok(
        await api.patch(
            "/api/v1/me/profile",
            headers=owner["headers"],
            json={
                "version": profile["version"],
                "bio": "Научный профиль",
                "contacts": [
                    {"type": "email", "value": "public@example.org", "visibility": "public"},
                    {
                        "type": "email",
                        "value": "members@example.org",
                        "visibility": "authenticated",
                    },
                    {"type": "phone", "value": "private", "visibility": "private"},
                ],
            },
        )
    )
    assert len(updated["contacts"]) == 3
    assert len(ok(await api.get(f"/api/v1/users/{owner['id']}"))["contacts"]) == 1
    assert (
        len(ok(await api.get(f"/api/v1/users/{owner['id']}", headers=other["headers"]))["contacts"])
        == 2
    )
    assert (
        await api.patch(
            "/api/v1/me/profile",
            headers=owner["headers"],
            json={"version": profile["version"], "bio": "Устаревшее"},
        )
    ).status_code == 409
    rotated = ok(
        await api.post("/api/v1/auth/refresh", json={"refresh_token": owner["refresh_token"]})
    )
    assert (
        await api.post("/api/v1/auth/refresh", json={"refresh_token": owner["refresh_token"]})
    ).status_code == 401
    assert (
        await api.get(
            "/api/v1/me/profile", headers={"Authorization": "Bearer " + rotated["access_token"]}
        )
    ).status_code == 401


async def test_project_last_seat_race_and_idempotency(api, users):
    owner, first, second = users
    row = await project(api, owner, size=2)
    applications = []
    for candidate in [first, second]:
        applications.append(
            ok(
                await command(
                    api,
                    candidate,
                    "POST",
                    f"/api/v1/projects/{row['id']}/applications",
                    {"message": "Могу помочь в исследовании"},
                ),
                201,
            )
        )
    results = await asyncio.gather(
        *[
            command(
                api,
                owner,
                "POST",
                f"/api/v1/projects/{row['id']}/applications/{application['id']}/accept",
                {},
            )
            for application in applications
        ]
    )
    assert sorted(response.status_code for response in results) == [200, 409], [
        r.text for r in results
    ]
    final = ok(await api.get(f"/api/v1/projects/{row['id']}"))
    assert final["members_count"] == 2 and final["free_slots"] == 0
    key = uuid4().hex
    body = {"title": "Идемпотентный проект"}
    first_result = ok(await command(api, owner, "POST", "/api/v1/projects", body, key), 201)
    replay = ok(await command(api, owner, "POST", "/api/v1/projects", body, key), 201)
    assert first_result == replay
    assert (
        await command(api, owner, "POST", "/api/v1/projects", {"title": "Другое содержимое"}, key)
    ).status_code == 409


async def test_private_invitation_does_not_grant_read_before_accept(api, users):
    owner, invited, outsider = users
    row = await project(api, owner, access="private_invite")
    path = f"/api/v1/projects/{row['id']}"
    assert (await api.get(path)).status_code == 404
    assert (await api.get(path, headers=outsider["headers"])).status_code == 404
    invitation = ok(
        await command(api, owner, "POST", path + "/invitations", {"user_id": invited["id"]}), 201
    )
    assert (await api.get(path, headers=invited["headers"])).status_code == 404
    preview = ok(await api.get("/api/v1/me/invitations", headers=invited["headers"]))
    assert any(item["id"] == invitation["id"] for item in preview["items"])
    ok(await command(api, invited, "POST", path + f"/invitations/{invitation['id']}/accept", {}))
    assert (await api.get(path, headers=invited["headers"])).status_code == 200
    assert row["id"] not in {item["id"] for item in ok(await api.get("/api/v1/projects"))["items"]}
    ok(await api.put(f"/api/v1/favorites/project/{row['id']}", headers=invited["headers"]), 204)
    ok(await command(api, invited, "POST", path + "/leave"), 204)
    favorite = ok(await api.get("/api/v1/favorites?type=project", headers=invited["headers"]))[
        "items"
    ][0]
    assert favorite["availability"] == "unavailable" and favorite["card"] is None


async def test_pdf_upload_scan_publish_and_explicit_coauthor_permissions(api, users):
    owner, coauthor, outsider = users
    area = ok(await api.get("/api/v1/dictionaries/research_areas"))["items"][0]["id"]
    paper = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/papers",
            {
                "title": "Статья для проверки",
                "abstract": "Проверка загрузки, сканирования и авторства",
                "area_id": area,
                "year": 2026,
                "authors": [{"display_name": "Тест Участник1"}],
            },
        ),
        201,
    )
    assert (await api.get(f"/api/v1/papers/{paper['id']}")).status_code == 404
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    payload = buffer.getvalue()
    upload = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/media/uploads",
            {
                "purpose": "paper_pdf",
                "resource_id": paper["id"],
                "filename": "paper.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(payload),
                "checksum_sha256": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
            },
        ),
        201,
    )
    async with httpx.AsyncClient(timeout=30) as s3:
        response = await s3.put(
            upload["upload_url"], content=payload, headers=upload["required_headers"]
        )
        assert response.status_code == 200, response.text
    ok(
        await command(api, owner, "POST", f"/api/v1/media/uploads/{upload['upload_id']}/complete"),
        202,
    )
    for _ in range(200):
        state = ok(
            await api.get(f"/api/v1/media/uploads/{upload['upload_id']}", headers=owner["headers"])
        )
        if state["status"] in {"ready", "rejected"}:
            break
        await asyncio.sleep(0.2)
    assert state["status"] == "ready", state
    paper = ok(
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=owner["headers"],
            json={"version": paper["version"], "file_id": upload["file_id"]},
        )
    )
    paper = ok(await command(api, owner, "POST", f"/api/v1/papers/{paper['id']}/publish"))
    assert (
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=coauthor["headers"],
            json={"version": paper["version"], "title": "Совпало ФИО"},
        )
    ).status_code == 403
    external = next(author for author in paper["authors"] if author["user_id"] is None)
    ok(
        await command(
            api,
            owner,
            "POST",
            f"/api/v1/papers/{paper['id']}/authors/{external['author_id']}/link",
            {"user_id": coauthor["id"]},
        )
    )
    paper = ok(await api.get(f"/api/v1/papers/{paper['id']}"))
    paper = ok(
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=coauthor["headers"],
            json={"version": paper["version"], "title": "Правка подтверждённого автора"},
        )
    )
    assert (
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=outsider["headers"],
            json={"version": paper["version"], "title": "Чужая правка"},
        )
    ).status_code == 403
    url = ok(await api.get(f"/api/v1/papers/{paper['id']}/file-url"))
    async with httpx.AsyncClient() as s3:
        response = await s3.get(url["url"])
        assert response.status_code == 200 and response.content == payload


@pytest.mark.parametrize("path", ["/api/v1/projects", "/api/v1/papers", "/api/v1/users"])
async def test_catalog_guests_and_validation(api, path):
    assert (await api.get(path)).status_code == 200
    response = await api.get(path, params={"limit": 101})
    assert response.status_code == 422
    assert response.json()["error"]["request_id"]
    assert (await api.get(path, params={"cursor": "invalid"})).status_code == 422
    assert (await api.get(path, headers={"Authorization": "Bearer garbage"})).status_code == 401


async def test_unauthorized_and_internal_boundaries(api):
    assert (await api.get("/api/v1/me/profile")).status_code == 401
    assert (await api.get("/api/v1/admin/users")).status_code == 401
    assert (await api.get("/internal/v1/users")).status_code == 404
    assert (await api.post("/api/v1/auth/register", json={"email": "not-email"})).status_code == 422
