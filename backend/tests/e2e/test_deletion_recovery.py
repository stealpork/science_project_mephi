import asyncio

import pytest

from tests.conftest import command, ok, project
from tests.e2e.test_file_lifecycle import join
from tests.files import publication
from tests.integration.test_infrastructure import compose

pytestmark = pytest.mark.e2e


async def deleted(api, job):
    for _ in range(400):
        result = ok(
            await api.get(
                f"/api/v1/account/deletion/{job['deletion_id']}",
                headers={"Authorization": "Bearer " + job["operation_token"]},
            )
        )
        if result["status"] == "completed":
            return result
        await asyncio.sleep(0.1)
    pytest.fail("Deletion did not recover after dependency restart")


@pytest.mark.parametrize("unavailable", ["publications", "people", "media", "activity"])
@pytest.mark.parametrize("users", [2], indirect=True)
async def test_account_deletion_recovers_after_partial_cross_service_commit(
    api, users, unavailable
):
    owner, successor = users
    row = await project(api, owner, access="private_invite")
    await join(api, owner, successor, row["id"])
    paper = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/papers",
            {
                "title": "Черновик не должен стать публичным",
                "project_id": row["id"],
                "authors": [{"user_id": successor["id"], "display_name": "Явный соавтор"}],
            },
        ),
        201,
    )
    compose("stop", "-t", "10", unavailable)
    try:
        job = ok(
            await command(
                api,
                owner,
                "DELETE",
                "/api/v1/account/me",
                {
                    "current_password": owner["password"],
                    "confirm": True,
                },
            ),
            202,
        )
        assert (await api.get("/api/v1/account/me", headers=owner["headers"])).status_code in {
            401,
            403,
        }
        for _ in range(150):
            result = ok(
                await api.get(f"/api/v1/projects/{row['id']}", headers=successor["headers"])
            )
            if result["owner_id"] == successor["id"]:
                break
            await asyncio.sleep(0.1)
        assert result["owner_id"] == successor["id"]
    finally:
        compose("start", "--wait", unavailable)
    await deleted(api, job)
    preserved = ok(await api.get(f"/api/v1/papers/{paper['id']}", headers=successor["headers"]))
    assert preserved["owner_id"] == successor["id"] and preserved["status"] == "draft"
    assert (await api.get(f"/api/v1/papers/{paper['id']}")).status_code == 404
    assert (await api.get(f"/api/v1/projects/{row['id']}")).status_code == 404
    assert (await api.get(f"/api/v1/users/{owner['id']}")).status_code == 404


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_sole_published_authorship_survives_but_archived_material_stays_hidden(api, users):
    owner = users[0]
    published = await publication(api, owner)
    archived = ok(
        await command(api, owner, "POST", "/api/v1/papers", {"title": "Удалённый ранее черновик"}),
        201,
    )
    ok(await command(api, owner, "DELETE", f"/api/v1/papers/{archived['id']}"))
    job = ok(
        await command(
            api,
            owner,
            "DELETE",
            "/api/v1/account/me",
            {
                "current_password": owner["password"],
                "confirm": True,
            },
        ),
        202,
    )
    await deleted(api, job)
    saved = ok(await api.get(f"/api/v1/papers/{published['id']}"))
    assert saved["status"] == "published" and saved["owner_id"] is None
    assert not saved["viewer"]["can_edit"] and not saved["viewer"]["can_manage_authors"]
    assert all(author["user_id"] is None for author in saved["authors"])
    assert "аккаунт удалён" in saved["authors"][0]["display_name"]
    assert (await api.get(f"/api/v1/papers/{archived['id']}")).status_code == 404
    assert (await api.get(f"/api/v1/papers/{published['id']}/file-url")).status_code == 200
