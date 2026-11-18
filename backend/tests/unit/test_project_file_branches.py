from datetime import timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.responses import Response

from libs.platform.database import now, uid
from libs.platform.errors import APIError
from services.projects import files as module
from services.projects.models import FileReservation, ProjectFile, ProjectSetting
from tests.unit.support import database, endpoint, operation, request, rows

pytestmark = pytest.mark.unit


@pytest.fixture
def files(monkeypatch):
    app = FastAPI()
    project = Obj(id=uid(), owner_id=uid(), status="recruiting")
    get, member = AsyncMock(return_value=project), AsyncMock(return_value=Obj(role="researcher"))
    module.install(app, get, member)
    monkeypatch.setattr(module, "command", operation)
    return Obj(app=app, project=project, get=get, member=member)


def body(fn, **values):
    return fn.__annotations__["body"](**values)


@pytest.mark.parametrize(
    "state",
    ["new", "prior", "attachment-replacement", "paper-replacement", "coauthor", "no-member"],
)
async def test_reservation_permission_deduplication_and_replacement(files, monkeypatch, state):
    db, fn, actor, file_id = database(), endpoint(files.app, "internal_reserve"), uid(), uid()
    paper_id, old_file = (uid() if state in {"paper-replacement", "coauthor"} else None), uid()
    existing = FileReservation(
        id=uid(), actor_id=actor, file_id=file_id, project_id=files.project.id
    )
    if state == "prior":
        db.scalar.side_effect = [existing]
    elif "replacement" in state:
        old = ProjectFile(
            id=uid(),
            origin="paper" if paper_id else "attachment",
            paper_id=paper_id,
            uploaded_by=actor,
        )
        db.scalar.side_effect = [None, old, None]
    else:
        db.scalar.side_effect = [None, 1, 1, Obj(value=5)]
    if state in {"coauthor", "no-member"}:
        files.member.return_value = None
    monkeypatch.setattr(
        module, "internal_call", AsyncMock(return_value={"project_id": files.project.id})
    )
    values = body(
        fn,
        actor_id=actor,
        file_id=file_id,
        paper_id=paper_id,
        replaces_file_id=old_file if "replacement" in state else None,
    )
    if state == "no-member":
        with pytest.raises(APIError) as error:
            await fn(files.project.id, values, request(), db)
        assert error.value.status == 403
        db.add.assert_not_called()
    else:
        result = await fn(files.project.id, values, request(), db)
        assert result["file_id"] == file_id
        assert db.add.call_count == int(state != "prior")
        if state == "coauthor":
            assert module.internal_call.call_args.args[1] == "publications"


@pytest.mark.parametrize(
    "state,code",
    [
        ("missing-old", "REPLACEMENT_TARGET_CHANGED"),
        ("wrong-paper", "PAPER_FILE"),
        ("wrong-uploader", "FORBIDDEN"),
        ("pending", "REPLACEMENT_PENDING"),
        ("full", "PROJECT_FILE_LIMIT_REACHED"),
        ("wrong-coauthor-project", "FILE_TARGET_MISMATCH"),
    ],
)
async def test_reservation_rejects_unsafe_or_overquota_replacement(files, monkeypatch, state, code):
    db, fn, actor = database(), endpoint(files.app, "internal_reserve"), uid()
    values = body(
        fn,
        actor_id=actor,
        file_id=uid(),
        replaces_file_id=None if state in {"full", "wrong-coauthor-project"} else uid(),
        paper_id=uid() if state == "wrong-coauthor-project" else None,
    )
    if state == "full":
        db.scalar.side_effect = [None, 4, 1, Obj(value=5)]
    else:
        old = (
            None
            if state == "missing-old"
            else ProjectFile(
                origin="paper" if state == "wrong-paper" else "attachment",
                paper_id=uid() if state == "wrong-paper" else None,
                uploaded_by=uid() if state == "wrong-uploader" else actor,
            )
        )
        db.scalar.side_effect = [None, old, "pending"]
    if state == "wrong-coauthor-project":
        files.member.return_value = None
    monkeypatch.setattr(module, "internal_call", AsyncMock(return_value={"project_id": "other"}))
    with pytest.raises(APIError) as error:
        await fn(files.project.id, values, request(), db)
    assert error.value.code == code
    db.add.assert_not_called()


@pytest.mark.parametrize("state", ["new", "existing-active", "existing-inactive", "replace"])
@pytest.mark.parametrize("paper", [False, True])
async def test_attach_fulfills_reservation_and_reactivates_existing_slot(
    files, monkeypatch, state, paper
):
    db, actor, file_id = database(), uid(), uid()
    fn = endpoint(files.app, "internal_attach" if paper else "add_file")
    paper_id = uid() if paper else None
    existing = (
        ProjectFile(
            id=uid(),
            file_id=file_id,
            project_id=files.project.id,
            active=state == "existing-active",
        )
        if state.startswith("existing")
        else None
    )
    reservation = Obj(
        status="pending",
        expires_at=now() + timedelta(days=1),
        replaces_file_id=uid() if state == "replace" else None,
    )
    old = ProjectFile(
        id=uid(), origin="paper" if paper else "attachment", paper_id=paper_id, uploaded_by=actor
    )
    db.scalar.side_effect = (
        [existing, reservation] + ([old] if state == "replace" else []) + [Obj(value=5), 1, 1]
    )
    call = AsyncMock(
        return_value={"status": "ready", "purpose": "paper_pdf", "resource_id": paper_id}
    )
    monkeypatch.setattr(module, "internal_call", call)
    values = body(
        fn,
        file_id=file_id,
        reservation_id=uid(),
        title="Document",
        **({"actor_id": actor, "paper_id": paper_id} if paper else {}),
    )
    result = await fn(files.project.id, values, request(), *([] if paper else [{"id": actor}]), db)
    assert result["file_id"] == file_id
    if state == "existing-active":
        call.assert_not_awaited()
    else:
        assert reservation.status == "fulfilled"
        assert result["origin"] == ("paper" if paper else "attachment")
        if existing:
            assert existing.active and db.add.call_count == 0
        else:
            db.add.assert_called_once()


@pytest.mark.parametrize("error_status", [None, 403, 404, 503, "unexpected"])
@pytest.mark.parametrize("authenticated", [False, True])
async def test_file_catalog_hides_inaccessible_papers_but_propagates_outages(
    files, monkeypatch, error_status, authenticated
):
    db, fn = database(), endpoint(files.app, "files")
    entry = ProjectFile(
        id=uid(), project_id=files.project.id, file_id=uid(), paper_id=uid(), active=True
    )
    db.scalars.return_value = rows([entry])
    db.scalar.side_effect = [1, 1, Obj(value=5)]
    failure = (
        RuntimeError("unexpected")
        if error_status == "unexpected"
        else APIError(error_status, "FAILED", "failed")
        if error_status
        else None
    )
    monkeypatch.setattr(module, "internal_call", AsyncMock(side_effect=failure, return_value={}))
    if error_status in {503, "unexpected"}:
        with pytest.raises(RuntimeError if error_status == "unexpected" else APIError):
            await fn(files.project.id, request(), {"id": uid()} if authenticated else None, db)
    else:
        result = await fn(files.project.id, request(), {"id": uid()} if authenticated else None, db)
        assert len(result["items"]) == int(error_status is None)
        assert result["can_upload"] is authenticated


@pytest.mark.parametrize("paper", [False, True])
@pytest.mark.parametrize("authenticated", [False, True])
async def test_file_download_authorizes_linked_paper_and_sets_no_store(
    files, monkeypatch, paper, authenticated
):
    db, fn, response = database(), endpoint(files.app, "download"), Response()
    entry = ProjectFile(
        id=uid(), project_id=files.project.id, file_id=uid(), paper_id=uid() if paper else None
    )
    db.scalar.return_value = entry
    call = AsyncMock(return_value={"url": "https://files.invalid/file"})
    monkeypatch.setattr(module, "internal_call", call)
    result = await fn(
        files.project.id,
        entry.file_id,
        request(),
        response,
        "attachment",
        {"id": uid()} if authenticated else None,
        db,
    )
    assert result["url"].startswith("https://") and response.headers["Cache-Control"] == "no-store"
    assert call.await_count == (2 if paper else 1)
    assert call.call_args.kwargs["json"]["mode"] == "attachment"


async def test_public_reservation_and_delete_attachment(files, monkeypatch):
    db, actor = database(), uid()
    fn = endpoint(files.app, "public_reserve")
    call = AsyncMock(
        return_value={"status": "ready", "resource_id": files.project.id, "purpose": "project_pdf"}
    )
    monkeypatch.setattr(module, "internal_call", call)
    db.scalar.side_effect = [None, 0, 0, Obj(value=5)]
    result = await fn(files.project.id, body(fn, file_id=uid()), request(), {"id": actor}, db)
    assert result["actor_id"] == actor
    entry = ProjectFile(id=uid(), origin="attachment", uploaded_by=actor, active=True)
    db.scalar.side_effect = None
    db.scalar.return_value = entry
    await endpoint(files.app, "delete_file")(files.project.id, uid(), {"id": actor}, db)
    assert entry.active is False


async def test_admin_file_policy_changes_are_versioned_and_locked(files):
    db = database()
    db.refresh = AsyncMock()
    setting = ProjectSetting(id=uid(), value=5, version=1)
    db.scalar.return_value = setting
    assert await endpoint(files.app, "get_policy")(db) == {"max_files_per_project": 5, "version": 1}
    fn = endpoint(files.app, "edit_policy")
    assert await fn(body(fn, version=1, max_files_per_project=10), db) == {
        "max_files_per_project": 10,
        "version": 2,
    }
    db.refresh.assert_awaited_once_with(setting, with_for_update=True)


async def test_attachment_without_paper_does_not_call_publication_service(files, monkeypatch):
    db = database()
    db.scalars.return_value = rows([ProjectFile(id=uid(), file_id=uid(), paper_id=None)])
    db.scalar.side_effect = [1, 0, Obj(value=5)]
    call = AsyncMock()
    monkeypatch.setattr(module, "internal_call", call)
    result = await endpoint(files.app, "files")(files.project.id, request(), None, db)
    assert len(result["items"]) == 1
    call.assert_not_awaited()
