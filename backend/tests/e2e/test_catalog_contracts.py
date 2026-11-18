from uuid import uuid4

import pytest

from libs.platform.schemas import Page, PaperDTO, ProfileDTO, ProjectDTO
from tests.conftest import command, ok, project

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    "path,model", [("users", ProfileDTO), ("projects", ProjectDTO), ("papers", PaperDTO)]
)
async def test_core_catalog_response_schema(api, path, model):
    result = ok(await api.get(f"/api/v1/{path}?limit=5"))
    Page[model].model_validate(result)


async def test_cursor_is_bounded_bound_to_filters_and_has_no_repeated_rows(api):
    first = ok(await api.get("/api/v1/users?limit=2&sort=name"))
    assert first["has_more"] and first["next_cursor"]
    cursor = first["next_cursor"]
    assert all(row["id"] not in cursor for row in first["items"])
    second = ok(
        await api.get("/api/v1/users", params={"limit": 2, "sort": "name", "cursor": cursor})
    )
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    assert (
        await api.get("/api/v1/users", params={"limit": 2, "sort": "newest", "cursor": cursor})
    ).status_code == 422
    assert (
        await api.get("/api/v1/users", params={"cursor": cursor[:-5] + "wrong"})
    ).status_code == 422
    assert (await api.get("/api/v1/users?sort=does-not-exist")).status_code == 422


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_catalog_deadline_sort_and_creation_date_filter(api, users):
    owner = users[0]
    prefix = "Сортировка " + uuid4().hex
    rows = []
    for index, deadline in enumerate(["2026-12-01T00:00:00Z", None, "2026-11-20T00:00:00Z"]):
        row = await project(api, owner)
        row = ok(
            await api.patch(
                f"/api/v1/projects/{row['id']}",
                headers=owner["headers"],
                json={
                    "version": row["version"],
                    "title": prefix + str(index),
                    "deadline": deadline,
                },
            )
        )
        rows.append(row)
    result = ok(
        await api.get("/api/v1/projects", params={"q": prefix, "sort": "deadline", "limit": 2})
    )
    assert [row["id"] for row in result["items"]] == [rows[2]["id"], rows[0]["id"]]
    result = ok(
        await api.get(
            "/api/v1/projects",
            params={"q": prefix, "sort": "deadline", "limit": 2, "cursor": result["next_cursor"]},
        )
    )
    assert [row["id"] for row in result["items"]] == [rows[1]["id"]]
    assert not ok(
        await api.get(
            "/api/v1/projects", params={"q": prefix, "created_to": "2020-01-01T00:00:00Z"}
        )
    )["items"]
    assert (await api.get("/api/v1/projects?created_from=not-a-date")).status_code == 422


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_wrong_dictionary_kind_cannot_be_stored_as_research_area(api, users):
    owner = users[0]
    skill = ok(await api.get("/api/v1/dictionaries/skills"))["items"][0]["id"]
    response = await command(
        api, owner, "POST", "/api/v1/projects", {"title": "Неверный справочник", "area_id": skill}
    )
    assert response.status_code == 422
    response = await command(
        api,
        owner,
        "POST",
        "/api/v1/papers",
        {"title": "Неверное направление статьи", "area_id": skill},
    )
    assert response.status_code == 422
