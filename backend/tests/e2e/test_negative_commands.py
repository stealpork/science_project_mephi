from uuid import uuid4

import pyotp
import pytest

from tests.conftest import command, ok, project
from tests.e2e.test_security import admin_credentials as admin_credentials

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_null_patch_fields_fail_validation_without_corrupting_versions(api, users):
    owner = users[0]
    profile = ok(await api.get("/api/v1/me/profile", headers=owner["headers"]))
    row = await project(api, owner)
    paper = ok(
        await command(api, owner, "POST", "/api/v1/papers", {"title": "Валидация PATCH"}), 201
    )
    cases = [
        (
            "/api/v1/me/profile",
            profile,
            [
                "first_name",
                "last_name",
                "bio",
                "contacts",
                "experience",
                "skill_ids",
                "interest_ids",
            ],
        ),
        (
            f"/api/v1/projects/{row['id']}",
            row,
            ["title", "description", "required_roles", "team_size", "results"],
        ),
        (
            f"/api/v1/papers/{paper['id']}",
            paper,
            ["title", "abstract", "keywords", "external_links"],
        ),
    ]
    for path, before, fields in cases:
        for field in fields:
            result = await api.patch(
                path, headers=owner["headers"], json={"version": before["version"], field: None}
            )
            assert result.status_code == 422, (field, result.text)
            assert result.json()["error"]["code"] == "VALIDATION_ERROR"
        assert ok(await api.get(path, headers=owner["headers"]))["version"] == before["version"]


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_profile_dictionary_hierarchy_and_search_filters(api, users, admin_credentials):
    owner = users[0]
    email, password = admin_credentials
    login = ok(await api.post("/api/v1/auth/login", json={"email": email, "password": password}))
    admin = {"headers": {"Authorization": "Bearer " + login["access_token"]}}

    async def entry(kind, **values):
        return ok(
            await command(
                api,
                admin,
                "POST",
                f"/api/v1/admin/dictionaries/{kind}",
                {"name": uuid4().hex, **values},
            ),
            201,
        )

    first, second = await entry("universities"), await entry("universities")
    faculty = await entry("faculties", parent_id=first["id"])
    department = await entry("departments", parent_id=faculty["id"])
    specialty = await entry("specialties")
    city = await entry("cities")
    skill = await entry("skills", skill_kind="technology")
    area = await entry("research_areas")
    interest = await entry("research_interests", parent_id=area["id"])
    profile = ok(await api.get("/api/v1/me/profile", headers=owner["headers"]))
    for values, error in [
        ({"university_id": second["id"], "faculty_id": faculty["id"]}, "INVALID_FACULTY"),
        ({"department_id": department["id"]}, "INVALID_DEPARTMENT"),
        ({"university_id": skill["id"]}, "INVALID_DICTIONARY_VALUE"),
    ]:
        response = await api.patch(
            "/api/v1/me/profile",
            headers=owner["headers"],
            json={"version": profile["version"], **values},
        )
        assert response.status_code == 422 and response.json()["error"]["code"] == error
    changed = ok(
        await api.patch(
            "/api/v1/me/profile",
            headers=owner["headers"],
            json={
                "version": profile["version"],
                "university_id": first["id"],
                "faculty_id": faculty["id"],
                "department_id": department["id"],
                "specialty_id": specialty["id"],
                "city_id": city["id"],
                "course": 3,
                "skill_ids": [skill["id"], skill["id"]],
                "interest_ids": [interest["id"]],
                "experience": [{"title": "Самостоятельное исследование", "completed": True}],
            },
        )
    )
    assert len(changed["skills"]) == 1
    result = ok(
        await api.get(
            "/api/v1/users",
            params={
                "department_id": department["id"],
                "skill_id": skill["id"],
                "skills_mode": "all",
                "interest_id": interest["id"],
                "has_project_experience": "true",
                "course": 3,
            },
        )
    )
    assert [item["id"] for item in result["items"]] == [owner["id"]]
    areas = ok(await api.get("/api/v1/users", params={"area_id": area["id"]}))
    assert [item["id"] for item in areas["items"]] == [owner["id"]]
    assert not ok(await api.get("/api/v1/users", params={"area_id": str(uuid4())}))["items"]
    assert (await api.get("/api/v1/users?course=invalid")).status_code == 422
    assert (await api.get("/api/v1/dictionaries/not-a-dictionary")).status_code == 404
    recommendations = ok(await api.get("/api/v1/recommendations/users", headers=owner["headers"]))
    assert owner["id"] not in {item["id"] for item in recommendations["items"]}


async def test_registration_rejects_invalid_reference_before_creating_account(api):
    skill = ok(await api.get("/api/v1/dictionaries/skills"))["items"][0]["id"]
    response = await api.post(
        "/api/v1/auth/register",
        json={
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": f"bad-{uuid4().hex}@example.org",
            "password": "Valid-test-password-2026!",
            "university_id": skill,
        },
    )
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "INVALID_DICTIONARY_KIND"
    )


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_mfa_can_be_disabled_only_with_password_and_current_otp(api, users):
    owner = users[0]
    password = {"current_password": owner["password"]}
    assert (
        await api.post(
            "/api/v1/account/2fa/enable", headers=owner["headers"], json={"code": "123456"}
        )
    ).status_code == 400
    assert (
        await api.post(
            "/api/v1/account/2fa/setup",
            headers=owner["headers"],
            json={"current_password": "wrong"},
        )
    ).status_code == 403
    setup = ok(await api.post("/api/v1/account/2fa/setup", headers=owner["headers"], json=password))
    from urllib.parse import parse_qs, urlparse

    secret = parse_qs(urlparse(setup["provisioning_uri"]).query)["secret"][0]
    code = pyotp.TOTP(secret).now()
    ok(await api.post("/api/v1/account/2fa/enable", headers=owner["headers"], json={"code": code}))
    assert (
        await api.post(
            "/api/v1/account/2fa/disable",
            headers=owner["headers"],
            json={"current_password": "wrong", "code": code},
        )
    ).status_code == 403
    assert (
        await api.post(
            "/api/v1/account/2fa/disable",
            headers=owner["headers"],
            json={**password, "code": "invalid"},
        )
    ).status_code in {403, 422}
    result = ok(
        await api.post(
            "/api/v1/account/2fa/disable",
            headers=owner["headers"],
            json={**password, "code": pyotp.TOTP(secret).now()},
        )
    )
    assert result == {"enabled": False}
    pair = ok(
        await api.post(
            "/api/v1/auth/login", json={"email": owner["email"], "password": owner["password"]}
        )
    )
    assert "access_token" in pair and "challenge_token" not in pair


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_media_target_and_claim_errors_remain_authorized(api, users):
    owner = users[0]
    profile = ok(await api.get("/api/v1/me/profile", headers=owner["headers"]))
    assert (await api.get(f"/api/v1/users/{owner['id']}/avatar-url")).status_code == 404
    result = await api.patch(
        "/api/v1/me/profile",
        headers=owner["headers"],
        json={"version": profile["version"], "avatar_file_id": str(uuid4())},
    )
    assert result.status_code == 404
    assert (
        await api.get(f"/api/v1/media/uploads/{uuid4()}", headers=owner["headers"])
    ).status_code == 404
    assert (
        await api.delete(f"/api/v1/media/uploads/{uuid4()}", headers=owner["headers"])
    ).status_code == 404


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_administrative_catalogs_and_failed_actions_have_audit(api, users, admin_credentials):
    email, password = admin_credentials
    login = ok(await api.post("/api/v1/auth/login", json={"email": email, "password": password}))
    admin = {"headers": {"Authorization": "Bearer " + login["access_token"]}}
    owner = users[0]
    paper = ok(
        await command(api, owner, "POST", "/api/v1/papers", {"title": "Модерация черновика"}), 201
    )
    for operation in ["hide", "restore"]:
        action = ok(
            await command(
                api,
                admin,
                "POST",
                f"/api/v1/admin/papers/{paper['id']}/{operation}",
                {"reason": "Проверка доступа и аудита"},
            ),
            202,
        )
        assert action["status"] == "succeeded"
        assert (
            ok(
                await api.get(
                    f"/api/v1/admin/actions/{action['action_id']}", headers=admin["headers"]
                )
            )["status"]
            == "succeeded"
        )
    for path in ["users", "projects", "papers", "audit"]:
        result = ok(
            await api.get(
                f"/api/v1/admin/{path}",
                headers=admin["headers"],
                params={"q": "Тест"} if path != "audit" else {},
            )
        )
        assert "items" in result
    assert (
        ok(await api.get(f"/api/v1/admin/users/{owner['id']}", headers=admin["headers"]))["id"]
        == owner["id"]
    )
    assert (
        await command(
            api,
            admin,
            "POST",
            f"/api/v1/admin/papers/{paper['id']}/destroy",
            {"reason": "Недопустимая команда"},
        )
    ).status_code == 422
