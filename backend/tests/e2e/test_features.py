import asyncio
import base64
import hashlib
import os
import re
from datetime import UTC, datetime

import httpx
import pytest

from tests.conftest import command, ok, project

pytestmark = pytest.mark.e2e


async def mail_token(email, subject, path):
    async with httpx.AsyncClient(
        base_url=os.environ.get("TEST_MAIL_URL", "http://localhost:18026"), timeout=10
    ) as mail:
        for _ in range(100):
            messages = ok(await mail.get("/api/v1/messages"))["messages"]
            for item in messages:
                if item["Subject"] == subject and any(
                    person["Address"] == email for person in item["To"]
                ):
                    body = ok(await mail.get("/api/v1/message/" + item["ID"]))
                    match = re.search(r"/" + path + r"\?token=([A-Za-z0-9_-]+)", body["Text"])
                    assert match, "Mail did not contain the expected action URL"
                    return match[1]
            await asyncio.sleep(0.1)
    pytest.fail("Outbox did not send mail through real SMTP")


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_real_smtp_verification_and_password_reset(api, users):
    user = users[0]
    secret = await mail_token(user["email"], "Подтверждение email", "verify-email")
    assert ok(await api.post("/api/v1/auth/verify-email", json={"token": secret}))["verified"]
    assert (await api.post("/api/v1/auth/verify-email", json={"token": secret})).status_code == 400
    known = await api.post("/api/v1/auth/password/forgot", json={"email": user["email"]})
    unknown = await api.post(
        "/api/v1/auth/password/forgot", json={"email": "not-registered@example.org"}
    )
    assert known.status_code == unknown.status_code == 202 and known.json() == unknown.json()
    secret = await mail_token(user["email"], "Восстановление пароля", "reset-password")
    ok(
        await api.post(
            "/api/v1/auth/password/reset",
            json={"token": secret, "new_password": "Another-password-2026!"},
        )
    )
    assert (await api.get("/api/v1/account/me", headers=user["headers"])).status_code == 401
    assert (
        await api.post(
            "/api/v1/auth/password/reset",
            json={"token": secret, "new_password": "Unwanted-password-2026!"},
        )
    ).status_code == 400
    assert (
        await api.post(
            "/api/v1/auth/login",
            json={"email": user["email"], "password": "Another-password-2026!"},
        )
    ).status_code == 200


@pytest.mark.parametrize("users", [2], indirect=True)
async def test_recommendations_recalculate_after_profile_change(api, users):
    owner, candidate = users
    row = await project(api, owner)
    skills = ok(await api.get("/api/v1/dictionaries/skills?limit=100"))["items"]
    interests = ok(await api.get("/api/v1/dictionaries/research_interests"))["items"]
    skill, interest = skills[0]["id"], interests[0]["id"]
    ok(
        await api.patch(
            f"/api/v1/projects/{row['id']}",
            headers=owner["headers"],
            json={
                "version": row["version"],
                "required_skill_ids": [skill],
                "interest_ids": [interest],
            },
        )
    )
    profile = ok(await api.get("/api/v1/me/profile", headers=candidate["headers"]))
    profile = ok(
        await api.patch(
            "/api/v1/me/profile",
            headers=candidate["headers"],
            json={"version": profile["version"], "skill_ids": [skill], "interest_ids": [interest]},
        )
    )
    matches = ok(
        await api.get("/api/v1/recommendations/projects?limit=100", headers=candidate["headers"])
    )
    match = next(item for item in matches["items"] if item["project"]["id"] == row["id"])
    assert match["score"] == 100 and match["reasons"]
    ok(
        await api.patch(
            "/api/v1/me/profile",
            headers=candidate["headers"],
            json={"version": profile["version"], "skill_ids": []},
        )
    )
    matches = ok(
        await api.get("/api/v1/recommendations/projects?limit=100", headers=candidate["headers"])
    )
    assert (
        next(item for item in matches["items"] if item["project"]["id"] == row["id"])["score"] == 57
    )


@pytest.mark.parametrize("users", [2], indirect=True)
async def test_notifications_favorites_reports_and_owner_scoping(api, users):
    owner, candidate = users
    row = await project(api, owner)
    ok(
        await command(
            api,
            candidate,
            "POST",
            f"/api/v1/projects/{row['id']}/applications",
            {"message": "Есть опыт анализа данных"},
        ),
        201,
    )
    for _ in range(100):
        notifications = ok(await api.get("/api/v1/notifications", headers=owner["headers"]))
        if notifications["items"]:
            break
        await asyncio.sleep(0.1)
    notification = notifications["items"][0]
    assert notification["target"]["id"] == row["id"]
    assert (
        await api.post(
            f"/api/v1/notifications/{notification['id']}/read", headers=candidate["headers"]
        )
    ).status_code == 404
    ok(
        await api.post(
            f"/api/v1/notifications/{notification['id']}/read", headers=owner["headers"]
        ),
        204,
    )
    ok(
        await api.post(
            "/api/v1/notifications/read-all",
            headers=owner["headers"],
            json={"before": datetime.now(UTC).isoformat()},
        ),
        204,
    )
    assert (
        ok(await api.get("/api/v1/notifications/unread-count", headers=owner["headers"]))["count"]
        == 0
    )
    ok(await api.put(f"/api/v1/favorites/project/{row['id']}", headers=candidate["headers"]), 204)
    assert (
        ok(await api.get(f"/api/v1/projects/{row['id']}", headers=candidate["headers"]))[
            "is_favorited"
        ]
        is True
    )
    ok(
        await api.delete(f"/api/v1/favorites/project/{row['id']}", headers=candidate["headers"]),
        204,
    )
    assert (
        ok(await api.get(f"/api/v1/projects/{row['id']}", headers=candidate["headers"]))[
            "is_favorited"
        ]
        is False
    )
    report = ok(
        await command(
            api,
            candidate,
            "POST",
            "/api/v1/reports",
            {
                "target_type": "project",
                "target_id": row["id"],
                "reason_code": "other",
                "description": "Проверка формы подачи жалобы",
            },
        ),
        201,
    )
    assert report["status"] == "open"


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_completed_experience_does_not_leak_after_project_becomes_private(api, users):
    owner = users[0]
    row = await project(api, owner)
    ok(await command(api, owner, "POST", f"/api/v1/projects/{row['id']}/start"))
    row = ok(await command(api, owner, "POST", f"/api/v1/projects/{row['id']}/complete"))
    for _ in range(100):
        profile = ok(await api.get(f"/api/v1/users/{owner['id']}"))
        if any(entry.get("project_id") == row["id"] for entry in profile["experience"]):
            break
        await asyncio.sleep(0.1)
    assert any(entry.get("project_id") == row["id"] for entry in profile["experience"])
    ok(
        await api.patch(
            f"/api/v1/projects/{row['id']}",
            headers=owner["headers"],
            json={"version": row["version"], "access_type": "private_invite"},
        )
    )
    profile = ok(await api.get(f"/api/v1/users/{owner['id']}"))
    assert all(entry.get("project_id") != row["id"] for entry in profile["experience"])
    assert (await api.get(f"/api/v1/projects/{row['id']}")).status_code == 404


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_invalid_pdf_is_rejected_after_real_s3_upload(api, users):
    owner = users[0]
    paper = ok(
        await command(api, owner, "POST", "/api/v1/papers", {"title": "Проверка невалидного PDF"}),
        201,
    )
    payload = b"<html>not a PDF</html>"
    upload = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/media/uploads",
            {
                "purpose": "paper_pdf",
                "resource_id": paper["id"],
                "filename": "fake.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(payload),
                "checksum_sha256": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
            },
        ),
        201,
    )
    async with httpx.AsyncClient(timeout=10) as s3:
        assert (
            await s3.put(upload["upload_url"], content=payload, headers=upload["required_headers"])
        ).status_code == 200
    ok(
        await command(api, owner, "POST", f"/api/v1/media/uploads/{upload['upload_id']}/complete"),
        202,
    )
    for _ in range(100):
        state = ok(
            await api.get(f"/api/v1/media/uploads/{upload['upload_id']}", headers=owner["headers"])
        )
        if state["status"] == "rejected":
            break
        await asyncio.sleep(0.1)
    assert state["status"] == "rejected" and state["rejection_code"] == "INVALID_PDF"
    assert (
        await api.patch(
            f"/api/v1/papers/{paper['id']}",
            headers=owner["headers"],
            json={"version": paper["version"], "file_id": upload["file_id"]},
        )
    ).status_code == 409


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_session_revocation_password_change_and_logout(api, users):
    user = users[0]
    other = ok(
        await api.post(
            "/api/v1/auth/login", json={"email": user["email"], "password": user["password"]}
        )
    )
    sessions = ok(await api.get("/api/v1/account/sessions", headers=user["headers"]))["items"]
    assert len(sessions) == 2 and all("refresh_hash" not in row for row in sessions)
    ok(
        await api.post(
            "/api/v1/account/password/change",
            headers=user["headers"],
            json={"current_password": user["password"], "new_password": "Changed-password-2026!"},
        )
    )
    assert (
        await api.get(
            "/api/v1/account/me", headers={"Authorization": "Bearer " + other["access_token"]}
        )
    ).status_code == 401
    assert (await api.get("/api/v1/account/me", headers=user["headers"])).status_code == 200
    response = await api.post("/api/v1/auth/logout", headers=user["headers"])
    assert response.status_code in {200, 204}
    assert (await api.get("/api/v1/account/me", headers=user["headers"])).status_code == 401
