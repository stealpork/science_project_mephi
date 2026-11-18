from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from libs.platform.database import uid
from libs.platform.errors import APIError
from services.publications import events
from services.publications.models import Author, Paper
from tests.unit.support import database, operation, request, rows, service

pytestmark = pytest.mark.unit


@pytest.fixture
def papers(monkeypatch):
    module = service(monkeypatch, "publications")
    monkeypatch.setattr(module, "command", operation)
    monkeypatch.setattr(module, "append_event", AsyncMock())
    return module


def paper(**changes):
    return Paper(
        **{
            "id": uid(),
            "creator_id": uid(),
            "owner_id": uid(),
            "title": "Research paper",
            "status": "published",
            "project_id": None,
            "file_id": None,
            "keywords": [],
            "version": 1,
            **changes,
        }
    )


@pytest.mark.parametrize("fault", [None, "invalid-year"])
async def test_paper_catalog_applies_metadata_and_author_filters(papers, monkeypatch, fault):
    db = database()
    query = (
        "area_id=area&university_id=university&author_user_id=author&author_name=Author&year_from="
        + ("bad" if fault else "2020")
        + "&year_to=2026&keyword=NLP"
    )
    paginate = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(papers, "paginate", paginate)
    monkeypatch.setattr(papers, "enrich_favorites", AsyncMock())
    if fault:
        with pytest.raises(APIError) as error:
            await papers.papers(request(query=query), "Research", 10, None, db)
        assert error.value.code == "INVALID_YEAR"
    else:
        assert (await papers.papers(request(query=query), "Research", 10, None, db))["items"] == []
        sql = str(paginate.call_args.args[1])
        assert "paper_authors.display_name" in sql and "paper_authors.user_id" in sql
        assert "research_papers.year >=" in sql and "research_papers.year <=" in sql


@pytest.mark.parametrize("relation", ["author", "owner", "creator"])
async def test_personal_paper_catalog_relations(papers, monkeypatch, relation):
    db = database()
    pagination = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(papers, "paginate", pagination)
    monkeypatch.setattr(papers, "enrich_favorites", AsyncMock())
    assert (await papers.my_papers(request(), relation, "draft", 10, {"id": "u"}, db))[
        "items"
    ] == []
    sql = str(pagination.call_args.args[1])
    assert "research_papers.status" in sql
    if relation == "creator":
        assert "research_papers.creator_id =" in sql


@pytest.mark.parametrize("fault", [None, "redis-down", "already-counted"])
@pytest.mark.parametrize("authenticated", [False, True])
async def test_view_counter_is_best_effort_and_deduplicated(
    papers, monkeypatch, fault, authenticated
):
    db, req, row = database(), request(), paper()
    db.refresh = AsyncMock()
    req.app.state.redis.set = AsyncMock(
        return_value=fault != "already-counted",
        side_effect=OSError("redis unavailable") if fault == "redis-down" else None,
    )
    monkeypatch.setattr(papers, "get_paper", AsyncMock(return_value=row))
    monkeypatch.setattr(papers, "view", AsyncMock(return_value={"id": row.id}))

    async def enrich(req, kind, user, cards):
        return cards

    monkeypatch.setattr(papers, "enrich_favorites", enrich)
    assert (await papers.paper_detail(row.id, req, {"id": "u"} if authenticated else None, db))[
        "id"
    ] == row.id
    assert db.execute.await_count == int(fault is None)
    assert db.refresh.await_count == int(fault is None)


@pytest.mark.parametrize(
    "change", ["file", "project-and-file", "move-existing-file", "keywords", "same-project"]
)
async def test_edit_paper_rebinds_files_and_normalizes_keywords(papers, monkeypatch, change):
    db, row, owner = database(), paper(project_id=uid(), file_id=uid()), uid()
    row.owner_id = owner
    monkeypatch.setattr(papers, "get_paper", AsyncMock(return_value=row))
    monkeypatch.setattr(papers, "view", AsyncMock(return_value={"id": row.id}))
    monkeypatch.setattr(
        papers, "internal_call", AsyncMock(return_value={"access_type": "private_invite"})
    )
    attach = AsyncMock()
    monkeypatch.setattr(papers, "attach_file", attach)
    old_project, old_file = row.project_id, row.file_id
    values = (
        {"file_id": uid()}
        if change == "file"
        else {"project_id": uid(), **({"file_id": uid()} if change == "project-and-file" else {})}
        if change in {"project-and-file", "move-existing-file"}
        else {"keywords": [" NLP ", "nlp", " "]}
        if change == "keywords"
        else {"project_id": old_project, "file_id": old_file}
    )
    await papers.edit_paper(
        row.id,
        papers.PaperPatch(version=1, confirm_visibility_expansion=True, **values),
        request(),
        {"id": owner},
        db,
    )
    if change == "keywords":
        assert row.keywords == ["nlp"]
        attach.assert_not_awaited()
    elif change == "same-project":
        attach.assert_not_awaited()
    else:
        attach.assert_awaited_once()
        if change == "file":
            assert attach.call_args.args[-1] == old_file
        elif change == "project-and-file":
            assert attach.call_args.args[-1] is None
    papers.append_event.assert_awaited_once()


async def test_author_metadata_can_be_changed_without_granting_account_rights(papers, monkeypatch):
    db, row = (
        database(),
        Author(
            id=uid(),
            paper_id=uid(),
            user_id=None,
            display_name="External",
            affiliation="Old",
            position=1,
        ),
    )
    monkeypatch.setattr(papers, "author_owned", AsyncMock(return_value=(paper(), row)))
    result = await papers.edit_author(
        row.paper_id,
        row.id,
        papers.AuthorPatch(display_name="Another person", affiliation="New", position=3),
        request(),
        {"id": "owner"},
        db,
    )
    assert result["user_id"] is None and row.position == 3 and row.display_name == "Another person"
    assert row.access_granted_at is None


async def test_author_link_and_removal_require_explicit_owner_grant(papers, monkeypatch):
    db, parent = database(), paper()
    author = Author(id=uid(), paper_id=parent.id, user_id=None, display_name="External")
    monkeypatch.setattr(papers, "author_owned", AsyncMock(return_value=(parent, author)))
    monkeypatch.setattr(papers, "internal_call", AsyncMock(return_value={"status": "active"}))
    target = uid()
    linked = await papers.link_author(
        parent.id,
        author.id,
        papers.LinkAuthor(user_id=target),
        request(),
        {"id": parent.owner_id},
        db,
    )
    assert linked["user_id"] == target and author.access_granted_at
    assert db.add.call_args.args[0].action == "author_linked"
    await papers.remove_author(parent.id, author.id, request(), {"id": parent.owner_id}, db)
    db.delete.assert_awaited_once_with(author)


async def test_owner_can_read_nonpublic_paper_without_author_row(papers):
    db, row = database(), paper(status="draft")
    db.scalar.side_effect = [row, None]
    assert await papers.get_paper(db, row.id, request(), row.owner_id, owner=True) is row


async def test_catalog_rechecks_rows_removed_between_queries(papers):
    db = database()
    db.scalars.return_value = rows([])
    result = await papers.catalog_cards(db, select(Paper), {"items": [{"id": "removed"}]}, None)
    assert result == []


async def test_non_paper_favorites_and_missing_papers_are_noops():
    db = database()
    await events.handle(None, db, {"payload": {"type": "project"}})
    db.scalar.assert_not_awaited()
    await events.handle(None, db, {"payload": {"type": "paper", "target_id": "missing"}})
    db.scalar.assert_awaited_once()


async def test_unlinked_paper_can_transfer_to_active_coauthor(monkeypatch):
    db, row = database(), paper()
    db.scalars.side_effect = [rows([row]), rows([Obj(user_id="successor")]), rows([])]
    monkeypatch.setattr(events, "internal_call", AsyncMock(return_value={"status": "active"}))
    await events.delete_user(request(), db, row.owner_id)
    assert row.owner_id == "successor"


async def test_paper_owner_can_delete_and_external_author_can_be_edited(papers, monkeypatch):
    db, parent = database(), paper()
    author = Author(id=uid(), paper_id=parent.id, user_id=None, display_name="External")
    db.get.return_value = author
    get = AsyncMock(return_value=parent)
    monkeypatch.setattr(papers, "get_paper", get)
    assert await papers.author_owned(
        db, parent.id, author.id, request(), {"id": parent.owner_id}
    ) == (parent, author)
    result = await papers.delete_paper(parent.id, request(), {"id": parent.owner_id}, db)
    assert result == {"status": "archived"} and parent.status == "archived"
    assert get.call_args.kwargs["owner"] and get.call_args.kwargs["lock"]


async def test_archived_paper_owner_cannot_remove_ownership_by_unlinking(papers, monkeypatch):
    db, parent = database(), paper()
    author = Author(id=uid(), paper_id=parent.id, user_id=uid(), display_name="Coauthor")
    monkeypatch.setattr(papers, "author_owned", AsyncMock(return_value=(parent, author)))
    result = await papers.unlink_author(
        parent.id, author.id, request(), {"id": parent.owner_id}, db
    )
    assert result["user_id"] is None and author.access_granted_at is None
    assert db.add.call_args.args[0].action == "author_unlinked"


async def test_owner_authorship_is_reused_in_creation_without_duplicate_grants(papers, monkeypatch):
    db, owner = database(), uid()
    calls = AsyncMock(return_value={"first_name": "Owner", "last_name": "Name"})
    monkeypatch.setattr(papers, "internal_call", calls)
    grants = AsyncMock()
    monkeypatch.setattr(papers, "grant_author", grants)
    monkeypatch.setattr(papers, "view", AsyncMock(return_value={"created": True}))
    own = papers.AuthorInput(user_id=owner, display_name="Explicit owner")
    external = papers.AuthorInput(display_name="External coauthor")
    assert await papers.create_paper(
        papers.PaperInput(title="Research paper", authors=[own, external]),
        request(),
        {"id": owner},
        db,
    ) == {"created": True}
    assert grants.await_count == 2 and grants.call_args_list[0].args[3] is own


async def test_readable_papers_batches_project_visibility_authority(papers, monkeypatch):
    db = database()
    db.scalars.return_value = rows(["a", "b"])
    call = AsyncMock(return_value={"items": [{"id": "a"}]})
    monkeypatch.setattr(papers, "internal_call", call)
    query = await papers.readable_papers(db, select(Paper), request(), "viewer")
    assert call.call_args.kwargs["json"] == {"actor_id": "viewer", "ids": ["a", "b"]}
    assert "research_papers.project_id IS NULL" in str(query)


async def test_private_paper_read_rechecks_project_access(papers, monkeypatch):
    db, row = database(), paper(project_id=uid())
    db.scalar.side_effect = [row, None]
    call = AsyncMock()
    monkeypatch.setattr(papers, "internal_call", call)
    assert await papers.get_paper(db, row.id, request(), None) is row
    assert call.call_args.kwargs["json"] == {"actor_id": None, "action": "read"}


async def test_add_author_requires_locked_owner_edit_rights_and_flushes_grant(papers, monkeypatch):
    db, parent = database(), paper()
    get = AsyncMock(return_value=parent)
    monkeypatch.setattr(papers, "get_paper", get)
    monkeypatch.setattr(papers, "internal_call", AsyncMock(return_value={"status": "active"}))
    target = papers.AuthorInput(user_id=uid(), display_name="Explicit coauthor")
    result = await papers.add_author(parent.id, target, request(), {"id": parent.owner_id}, db)
    assert result["user_id"] == target.user_id and result["access_granted_at"]
    assert get.call_args.kwargs == {"owner": True, "edit": True, "lock": True}
    assert db.add.call_count == 2
    db.flush.assert_awaited_once()
