from uuid import uuid4

import pytest

from tests.conftest import command, ok, project
from tests.e2e.test_security import admin_credentials as admin_credentials

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_administrator_changes_file_limit_and_moderates_suggestions(
    api, users, admin_credentials
):
    email, password = admin_credentials
    pair = ok(await api.post("/api/v1/auth/login", json={"email": email, "password": password}))
    admin = {"headers": {"Authorization": "Bearer " + pair["access_token"]}}
    user = users[0]
    policy = ok(await api.get("/api/v1/admin/settings/project-files", headers=admin["headers"]))
    assert (
        await api.get("/api/v1/admin/settings/project-files", headers=user["headers"])
    ).status_code == 403
    try:
        changed = ok(
            await api.patch(
                "/api/v1/admin/settings/project-files",
                headers=admin["headers"],
                json={
                    "version": policy["version"],
                    "max_files_per_project": 6,
                    "reason": "Проверка расширения лимита",
                },
            )
        )
        assert changed["max_files_per_project"] == 6
        assert (
            await api.patch(
                "/api/v1/admin/settings/project-files",
                headers=admin["headers"],
                json={
                    "version": policy["version"],
                    "max_files_per_project": 7,
                    "reason": "Устаревшая настройка",
                },
            )
        ).status_code == 409
    finally:
        current = ok(
            await api.get("/api/v1/admin/settings/project-files", headers=admin["headers"])
        )
        ok(
            await api.patch(
                "/api/v1/admin/settings/project-files",
                headers=admin["headers"],
                json={
                    "version": current["version"],
                    "max_files_per_project": policy["max_files_per_project"],
                    "reason": "Восстановление тестового значения",
                },
            )
        )
    target = ok(await api.get("/api/v1/dictionaries/skills"))["items"][0]["id"]
    for decision in ["merge", "reject"]:
        proposal = ok(
            await command(
                api,
                user,
                "POST",
                "/api/v1/dictionaries/suggestions",
                {"kind": "skills", "name": "Проверяемый навык " + uuid4().hex},
            ),
            201,
        )
        payload = (
            {"target_id": target}
            if decision == "merge"
            else {"reason": "Не является самостоятельным навыком"}
        )
        result = ok(
            await command(
                api,
                admin,
                "POST",
                f"/api/v1/admin/dictionary-suggestions/{proposal['id']}/{decision}",
                payload,
            )
        )
        assert result["status"] == ("merged" if decision == "merge" else "rejected")


async def test_team_roles_transfer_and_invite_only_rules(api, users):
    owner, member, outsider = users
    row = await project(api, owner, access="public_invite")
    root = f"/api/v1/projects/{row['id']}"
    assert (
        await command(
            api,
            outsider,
            "POST",
            root + "/applications",
            {"message": "Попытка вступить без приглашения"},
        )
    ).status_code == 409
    invitation = ok(
        await command(api, owner, "POST", root + "/invitations", {"user_id": member["id"]}), 201
    )
    ok(await command(api, member, "POST", root + f"/invitations/{invitation['id']}/accept", {}))
    assert (
        await api.patch(
            root + f"/members/{member['id']}", headers=outsider["headers"], json={"role": "analyst"}
        )
    ).status_code == 403
    ok(
        await api.patch(
            root + f"/members/{member['id']}", headers=owner["headers"], json={"role": "analyst"}
        )
    )
    assert (await command(api, owner, "POST", root + "/leave")).status_code == 409
    transferred = ok(
        await command(api, owner, "POST", root + "/transfer-ownership", {"user_id": member["id"]})
    )
    assert transferred["owner_id"] == member["id"]
    assert (await command(api, owner, "POST", root + "/start")).status_code == 403
    active = ok(await command(api, member, "POST", root + "/start"))
    assert active["status"] == "active"
    completed = ok(await command(api, member, "POST", root + "/complete"))
    assert completed["status"] == "completed"
    assert (await command(api, member, "POST", root + "/reopen")).status_code == 409
    archived = ok(await command(api, member, "DELETE", root))
    assert archived["status"] == "archived"
    assert not archived["viewer"]["can_manage"]
    assert (await api.get(root)).status_code == 404
    assert (await api.get(root, headers=member["headers"])).status_code == 200
    assert (
        await api.patch(
            root,
            headers=member["headers"],
            json={"version": archived["version"], "title": "Архив не изменяется"},
        )
    ).status_code == 403


async def test_registered_paper_author_loses_private_project_access_when_removed(api, users):
    owner, coauthor, outsider = users
    row = await project(api, owner, access="private_invite")
    root = f"/api/v1/projects/{row['id']}"
    invitation = ok(
        await command(api, owner, "POST", root + "/invitations", {"user_id": coauthor["id"]}), 201
    )
    ok(await command(api, coauthor, "POST", root + f"/invitations/{invitation['id']}/accept", {}))
    paper = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/papers",
            {
                "title": "Закрытая совместная работа",
                "project_id": row["id"],
                "authors": [{"user_id": coauthor["id"], "display_name": "Подтверждённый автор"}],
            },
        ),
        201,
    )
    paper_root = f"/api/v1/papers/{paper['id']}"
    assert (await api.get(paper_root, headers=coauthor["headers"])).status_code == 200
    assert (await api.get(paper_root, headers=outsider["headers"])).status_code == 404
    ok(await api.delete(root + f"/members/{coauthor['id']}", headers=owner["headers"]), 204)
    assert (await api.get(paper_root, headers=coauthor["headers"])).status_code == 404
    listed = ok(await api.get("/api/v1/me/papers", headers=coauthor["headers"]))
    assert paper["id"] not in {item["id"] for item in listed["items"]}
    assert (
        await api.patch(
            paper_root,
            headers=coauthor["headers"],
            json={"version": paper["version"], "title": "Доступ должен быть отозван"},
        )
    ).status_code == 404


@pytest.mark.parametrize("users", [2], indirect=True)
async def test_paper_ownership_and_author_unlink_are_explicit(api, users):
    owner, author = users
    paper = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/papers",
            {
                "title": "Явное управление авторами",
                "authors": [{"user_id": author["id"], "display_name": "Соавтор"}],
            },
        ),
        201,
    )
    root = f"/api/v1/papers/{paper['id']}"
    own_author = next(item for item in paper["authors"] if item["user_id"] == owner["id"])
    assert (
        await command(api, owner, "POST", root + f"/authors/{own_author['author_id']}/unlink")
    ).status_code == 409
    assert (
        await api.patch(
            root,
            headers=author["headers"],
            json={"version": paper["version"], "owner_id": author["id"]},
        )
    ).status_code == 422
    transferred = ok(
        await command(api, owner, "POST", root + "/transfer-ownership", {"user_id": author["id"]})
    )
    assert transferred["owner_id"] == author["id"]
    ok(await command(api, author, "POST", root + f"/authors/{own_author['author_id']}/unlink"))
    assert (
        await api.patch(
            root,
            headers=owner["headers"],
            json={"version": transferred["version"], "title": "Бывший автор"},
        )
    ).status_code == 404


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_failed_command_does_not_consume_idempotency_key(api, users):
    owner = users[0]
    key = uuid4().hex
    invalid = await command(
        api,
        owner,
        "POST",
        "/api/v1/projects",
        {"title": "Неверная роль", "required_roles": ["leader"]},
        key,
    )
    assert invalid.status_code == 422
    valid = await command(
        api, owner, "POST", "/api/v1/projects", {"title": "Исправленный запрос"}, key
    )
    assert valid.status_code == 201
