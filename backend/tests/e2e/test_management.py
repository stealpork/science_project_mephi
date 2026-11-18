from uuid import uuid4

import pytest

from tests.conftest import command, ok, project
from tests.e2e.test_security import admin_credentials as admin_credentials

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("users", [2], indirect=True)
async def test_management_lists_report_resolution_and_dictionary_crud(
    api, users, admin_credentials
):
    owner, candidate = users
    email, password = admin_credentials
    pair = ok(await api.post("/api/v1/auth/login", json={"email": email, "password": password}))
    admin = {"headers": {"Authorization": "Bearer " + pair["access_token"]}}
    row = await project(api, owner)
    root = f"/api/v1/projects/{row['id']}"
    for path in [
        "/api/v1/me/projects",
        "/api/v1/me/teams",
        "/api/v1/me/applications",
        "/api/v1/me/invitations",
        root + "/applications",
        root + "/invitations",
        root + "/members",
        root + "/files",
    ]:
        assert (await api.get(path, headers=owner["headers"])).status_code == 200
    application = ok(
        await command(
            api,
            candidate,
            "POST",
            root + "/applications",
            {"message": "Опыт для управленческого сценария"},
        ),
        201,
    )
    ok(
        await command(
            api,
            owner,
            "POST",
            root + f"/applications/{application['id']}/reject",
            {"reason": "Другие задачи исследования"},
        )
    )
    invitation = ok(
        await command(api, owner, "POST", root + "/invitations", {"user_id": candidate["id"]}), 201
    )
    ok(await command(api, candidate, "POST", root + f"/invitations/{invitation['id']}/decline", {}))
    for path in [
        "/api/v1/admin/users",
        f"/api/v1/admin/users/{owner['id']}",
        "/api/v1/admin/projects",
        "/api/v1/admin/papers",
        "/api/v1/admin/dictionary-suggestions",
    ]:
        assert (await api.get(path, headers=admin["headers"])).status_code == 200
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
                "description": "Проверка административного рассмотрения",
            },
        ),
        201,
    )
    assert (
        await api.get(
            "/api/v1/admin/reports?status=open&target_type=project", headers=admin["headers"]
        )
    ).status_code == 200
    report = ok(await api.get(f"/api/v1/admin/reports/{report['id']}", headers=admin["headers"]))
    resolved = ok(
        await api.patch(
            f"/api/v1/admin/reports/{report['id']}",
            headers=admin["headers"],
            json={
                "version": report["version"],
                "status": "resolved",
                "resolution": "Проверка выполнена",
            },
        )
    )
    assert resolved["status"] == "resolved"
    name = "Справочник " + uuid4().hex
    entry = ok(
        await command(
            api,
            admin,
            "POST",
            "/api/v1/admin/dictionaries/skills",
            {"name": name, "skill_kind": "technology"},
        ),
        201,
    )
    changed = ok(
        await api.patch(
            f"/api/v1/admin/dictionaries/skills/{entry['id']}",
            headers=admin["headers"],
            json={"version": entry["version"], "name": name + " изменён"},
        )
    )
    ok(
        await api.patch(
            f"/api/v1/admin/dictionaries/skills/{entry['id']}",
            headers=admin["headers"],
            json={"version": changed["version"], "active": False},
        )
    )
    result = ok(await api.get("/api/v1/dictionaries/skills", params={"q": name}))
    assert entry["id"] not in {value["id"] for value in result["items"]}
