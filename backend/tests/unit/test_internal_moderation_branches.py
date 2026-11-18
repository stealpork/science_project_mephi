from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from libs.platform.database import uid
from libs.platform.errors import APIError
from services.projects import extra as projects
from services.publications import extra as papers
from tests.unit.support import database, endpoint, request

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("module", [projects, papers])
@pytest.mark.parametrize(
    "state", ["prior", "hide", "restore", "blocked-actor", "regular-actor", "missing-target"]
)
async def test_internal_moderation_enforces_actor_and_records_idempotent_audit(
    monkeypatch, module, state
):
    db, app = database(), FastAPI()
    get, view = AsyncMock(), AsyncMock()
    module.install(app, get, view)
    row = Obj(owner_id="owner", moderation_status="visible")
    db.scalar.side_effect = [
        Obj(id="prior") if state == "prior" else None,
        None if state == "missing-target" else row,
    ]
    call = AsyncMock(
        return_value={
            "status": "blocked" if state == "blocked-actor" else "active",
            "roles": ["user"] if state == "regular-actor" else ["moderator"],
        }
    )
    monkeypatch.setattr(module, "internal_call", call)
    emit = AsyncMock()
    monkeypatch.setattr(module, "append_event", emit)
    body = module.ModerateInput(
        actor_id=uid(), action_id=uid(), hidden=state != "restore", reason="moderation reason"
    )
    if state in {"blocked-actor", "regular-actor", "missing-target"}:
        with pytest.raises(APIError) as error:
            await endpoint(app, "moderate")("target", body, request(), db)
        assert error.value.status == (404 if state == "missing-target" else 403)
        db.add.assert_not_called()
    else:
        result = await endpoint(app, "moderate")("target", body, request(), db)
        assert result["status"] == "succeeded"
        if state == "prior":
            call.assert_not_awaited()
            emit.assert_not_awaited()
        else:
            assert row.moderation_status == ("visible" if state == "restore" else "hidden")
            assert db.add.call_args.args[0].action == row.moderation_status
            emit.assert_awaited_once()


@pytest.mark.parametrize("module", [projects, papers])
@pytest.mark.parametrize("search", ["", "Research"])
async def test_internal_catalog_query_and_resource_projection(monkeypatch, module, search):
    db, app = database(), FastAPI()
    get, view = AsyncMock(return_value=Obj(id="target")), AsyncMock(return_value={"id": "target"})
    module.install(app, get, view)
    paginate = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(module, "paginate", paginate)
    assert await endpoint(app, "catalog")(request(), search, 10, db) == {"items": []}
    assert ("lower(" in str(paginate.call_args.args[1])) is bool(search)
    if module is projects:
        result = await endpoint(app, "read_resource")(
            "target", module.ResourceInput(actor_id=None), db
        )
    else:
        result = await endpoint(app, "read_resource")(
            "target", module.ResourceInput(actor_id=None), request(), db
        )
    assert result == {"id": "target"}


async def test_paper_reconciliation_returns_authority_state():
    db, app = database(), FastAPI()
    papers.install(app, AsyncMock(), AsyncMock())
    db.get.return_value = Obj(id="p", project_id="project", file_id="file", status="archived")
    assert await endpoint(app, "reconcile")("p", db) == {
        "paper_id": "p",
        "project_id": "project",
        "file_id": "file",
        "status": "archived",
    }
