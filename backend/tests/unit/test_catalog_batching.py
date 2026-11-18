import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from libs.platform import api
from libs.platform.config import Settings
from libs.platform.database import now, uid
from libs.platform.errors import APIError
from services.publications.models import Author, Paper

pytestmark = pytest.mark.unit


@pytest.fixture
def publications(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(service_name="publications"))
    return importlib.import_module("services.publications.main")


async def test_twenty_paper_cards_use_one_author_query(publications):
    papers = [
        Paper(id=uid(), owner_id="owner", creator_id="owner", status="published", title="Paper")
        for _ in range(20)
    ]
    authors = [
        Author(
            id=uid(),
            paper_id=paper.id,
            user_id="owner",
            display_name="Author",
            position=1,
            affiliation="",
            access_granted_at=now(),
        )
        for paper in papers
    ]
    scalar = AsyncMock(return_value=SimpleNamespace(all=lambda: authors))
    result = await publications.views(SimpleNamespace(scalars=scalar), papers, "owner")
    scalar.assert_awaited_once()
    assert [row["id"] for row in result] == [row.id for row in papers]
    assert all(
        row["viewer"]["can_edit"] and row["authors"][0]["display_name"] == "Author"
        for row in result
    )


async def test_empty_catalog_does_not_fetch_authors(publications):
    scalar = AsyncMock()
    assert await publications.views(SimpleNamespace(scalars=scalar), [], None) == []
    scalar.assert_not_awaited()


@pytest.mark.parametrize(
    "status,owner,viewer,can_edit",
    [
        ("published", "u", "u", True),
        ("draft", "u", "u", True),
        ("archived", "u", "u", False),
        ("published", None, "u", False),
        ("published", "u", None, False),
    ],
)
def test_paper_capabilities_respect_archive_and_orphan_state(
    publications, status, owner, viewer, can_edit
):
    paper = Paper(id=uid(), owner_id=owner, creator_id="u", status=status, title="Paper")
    author = Author(
        id=uid(), paper_id=paper.id, user_id="u", display_name="Author", position=1, affiliation=""
    )
    card = publications.paper_card(paper, [author], viewer)
    assert card["viewer"]["can_edit"] is can_edit
    assert card["viewer"]["can_manage_authors"] is can_edit


@pytest.mark.parametrize(
    "status,author,viewer,edit,expected",
    [
        ("draft", False, "outsider", False, 404),
        ("draft", True, "author", False, 200),
        ("archived", True, "author", True, 403),
        ("published", False, "outsider", True, 403),
        ("published", False, None, False, 200),
    ],
)
async def test_paper_read_and_edit_authorization(
    publications, status, author, viewer, edit, expected
):
    paper = Paper(id=uid(), owner_id="owner", creator_id="owner", status=status, project_id=None)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[paper, "author-id" if author else None]))
    if expected == 200:
        assert await publications.get_paper(db, paper.id, None, viewer, edit=edit) is paper
    else:
        with pytest.raises(APIError) as error:
            await publications.get_paper(db, paper.id, None, viewer, edit=edit)
        assert error.value.status == expected
