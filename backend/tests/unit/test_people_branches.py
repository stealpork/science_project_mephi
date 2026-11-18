from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest
from starlette.responses import Response

from libs.platform.database import uid
from libs.platform.errors import APIError
from services.people import events, extra
from services.people.models import DictionaryEntry, DictionarySuggestion, Profile
from tests.unit.support import database, endpoint, operation, request, rows, service

pytestmark = pytest.mark.unit


@pytest.fixture
def people(monkeypatch):
    module = service(monkeypatch, "people")
    monkeypatch.setattr(module, "command", operation)
    monkeypatch.setattr(module, "append_event", AsyncMock())
    return module


def profile(**changes):
    return Profile(
        **{
            "id": uid(),
            "user_id": uid(),
            "first_name": "Имя",
            "last_name": "Фамилия",
            "bio": "",
            "skills": [],
            "interests": [],
            "experience": [],
            "contacts": [],
            "version": 1,
            "blocked": False,
            "deleted": False,
            **changes,
        }
    )


@pytest.mark.parametrize("failure", [None, 503, 422])
@pytest.mark.parametrize("viewer", [None, {"id": "viewer"}])
async def test_platform_experience_is_rechecked_against_project_authority(
    people, monkeypatch, failure, viewer
):
    own = {
        "viewer": {"is_self": True},
        "experience": [{"source": "platform", "project_id": "private"}],
    }
    card = {
        "viewer": {"is_self": False},
        "experience": [
            {"source": "self_reported", "title": "Self"},
            {"source": "platform", "project_id": "visible", "title": "Stale"},
            {"source": "platform", "project_id": "private"},
        ],
    }
    call = AsyncMock(
        return_value={"items": [{"id": "visible", "title": "Current"}]},
        side_effect=APIError(failure, "UPSTREAM", "failed") if failure else None,
    )
    monkeypatch.setattr(people, "internal_call", call)
    if failure == 422:
        with pytest.raises(APIError):
            await people.secure_profiles(request(), [own, card], viewer)
    else:
        await people.secure_profiles(request(), [own, card], viewer)
        assert len(own["experience"]) == 1
        assert card["experience"] == (
            [{"source": "self_reported", "title": "Self"}]
            if failure
            else [
                {"source": "self_reported", "title": "Self"},
                {"source": "platform", "project_id": "visible", "title": "Current"},
            ]
        )
        assert call.call_args.kwargs["json"]["actor_id"] == (viewer["id"] if viewer else None)


async def test_profile_update_preserves_platform_experience_and_assigns_manual_ids(
    people, monkeypatch
):
    db, row = (
        database(),
        profile(
            experience=[
                {"id": "platform", "source": "platform"},
                {"id": "old", "source": "self_reported"},
            ]
        ),
    )
    db.scalar.return_value = row
    body = people.ProfilePatch(
        version=1,
        bio="New",
        experience=[
            people.Experience(title="Manual"),
            people.Experience(id=uid(), title="Existing"),
        ],
    )
    result = await people.edit_profile(body, request(), {"id": row.user_id}, db)
    assert result["bio"] == "New" and row.version == 2
    assert len(row.experience) == 3 and row.experience[0]["id"] == "platform"
    assert row.experience[1]["id"] and row.experience[2]["id"] == body.experience[1].id
    people.append_event.assert_awaited_once()


@pytest.mark.parametrize("present", [False, True])
async def test_own_profile_distinguishes_projection_initialization(people, present):
    db, row = database(), profile()
    db.scalar.return_value = row if present else None
    result = await people.own_profile({"id": row.user_id}, db)
    if present:
        assert result["completeness"] == 0 and result["viewer"]["is_self"]
    else:
        assert result.status_code == 202


@pytest.mark.parametrize("skills_mode", ["all", "any"])
async def test_user_search_constructs_every_filter_and_returns_safe_cards(
    people, monkeypatch, skills_mode
):
    db, row = database(), profile()
    query = (
        "university_id=u&faculty_id=f&department_id=d&specialty_id=s&course=2&city_id=c&skill_id=a&skill_id=b&interest_id=i&area_id=area&has_project_experience=true&skills_mode="
        + skills_mode
    )
    paginate = AsyncMock(return_value={"items": [{"id": row.id}]})
    monkeypatch.setattr(people, "paginate", paginate)
    monkeypatch.setattr(people, "enrich_favorites", AsyncMock())
    db.scalars.side_effect = [rows(["interest"]), rows([row])]
    result = await people.users(request(query=query), q="Имя", limit=10, user=None, db=db)
    assert result["items"][0]["id"] == row.user_id
    sql = str(paginate.call_args.args[1])
    for field in [
        "university_id",
        "faculty_id",
        "department_id",
        "specialty_id",
        "course",
        "city_id",
        "skills",
        "interests",
        "experience",
    ]:
        assert "profiles." + field in sql


@pytest.mark.parametrize("interests", [[], [{"id": "i"}]])
async def test_user_recommendations_use_interests_when_available(people, monkeypatch, interests):
    db, own, other = database(), profile(interests=interests), profile()
    db.scalar.side_effect = [own, other]
    monkeypatch.setattr(people, "enrich_favorites", AsyncMock())
    paginated = AsyncMock(return_value={"items": [{"user_id": other.user_id}]})
    monkeypatch.setattr(people, "paginate", paginated)
    result = await people.recommended_users(request(), 6, {"id": own.user_id}, db)
    assert result["items"][0]["id"] == other.user_id
    assert ("@>" in str(paginated.call_args.args[1])) is bool(interests)


async def test_dictionary_search_parent_kind_and_own_suggestions(people, monkeypatch):
    paginated = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(people, "paginate", paginated)
    await people.dictionary(
        "skills", request(query="faculty_id=f&skill_kind=technology"), "Python", 10, database()
    )
    sql = str(paginated.call_args.args[1])
    assert "parent_id" in sql and "skill_kind" in sql and "lower(dictionary_entries.name)" in sql
    assert await people.own_suggestions(request(), 10, {"id": "u"}, database()) == {"items": []}


@pytest.mark.parametrize(
    "parent_kind,kind",
    [
        ("universities", "faculties"),
        ("faculties", "departments"),
        ("research_areas", "research_interests"),
        ("universities", "skills"),
    ],
)
@pytest.mark.parametrize("existing", [False, True])
async def test_dictionary_creation_validates_parent_and_deduplicates(
    people, parent_kind, kind, existing
):
    db = database()
    db.get.return_value = Obj(id=uid(), kind=parent_kind, active=True)
    entry = DictionaryEntry(id=uid(), name="Existing", kind=kind)
    db.scalar.return_value = entry if existing else None
    result = await endpoint(people.app, "add_entry")(
        kind, extra.DictionaryInput(name=" New  Value ", parent_id=uid()), db
    )
    if existing:
        assert result["id"] == entry.id and db.add.call_count == 0
    else:
        assert result["normalized_name"] == "new value" and db.add.call_count == 1


@pytest.mark.parametrize(
    "parent", [None, Obj(kind="skills", active=True), Obj(kind="universities", active=False)]
)
async def test_dictionary_creation_rejects_wrong_missing_or_inactive_parent(people, parent):
    db = database()
    db.get.return_value = parent
    with pytest.raises(APIError) as error:
        await endpoint(people.app, "add_entry")(
            "faculties", extra.DictionaryInput(name="New", parent_id=uid()), db
        )
    assert error.value.code == "INVALID_PARENT"
    db.add.assert_not_called()


@pytest.mark.parametrize("changes", [{"name": "Renamed", "active": False}, {}])
async def test_dictionary_patch_distinguishes_omitted_fields(people, changes):
    db = database()
    row = DictionaryEntry(
        id=uid(), kind="skills", name="Old", active=True, normalized_name="old", version=1
    )
    db.scalar.return_value = row
    result = await endpoint(people.app, "edit_entry")(
        "skills", row.id, extra.DictionaryPatch(version=1, **changes), db
    )
    assert result["name"] == changes.get("name", "Old")
    assert result["active"] == changes.get("active", True)
    assert row.version == 2


@pytest.mark.parametrize("decision", ["approve", "merge", "reject", "already"])
async def test_suggestion_decisions_emit_one_notification_and_retain_reason(
    people, monkeypatch, decision
):
    db = database()
    suggestion = DictionarySuggestion(
        id=uid(),
        user_id=uid(),
        name="Python",
        kind="skills",
        status="approved" if decision == "already" else "pending",
    )
    entry = DictionaryEntry(id=uid(), kind="skills", name="Python", active=True)
    db.scalar.side_effect = [suggestion, entry]
    db.get.return_value = entry
    emit = AsyncMock()
    monkeypatch.setattr(extra, "append_event", emit)
    result = await endpoint(people.app, "decide")(
        suggestion.id,
        "approve" if decision == "already" else decision,
        extra.Decision(target_id=entry.id, reason="review reason"),
        db,
    )
    assert (
        result["status"]
        == {"approve": "approved", "merge": "merged", "reject": "rejected", "already": "approved"}[
            decision
        ]
    )
    assert emit.await_count == int(decision != "already")
    if decision != "already":
        assert suggestion.decision_reason == "review reason"


@pytest.mark.parametrize(
    "target", [None, Obj(kind="wrong", active=True), Obj(kind="skills", active=False)]
)
async def test_suggestion_merge_requires_explicit_active_same_kind_target(people, target):
    db = database()
    db.scalar.return_value = DictionarySuggestion(id=uid(), kind="skills", status="pending")
    db.get.return_value = target
    with pytest.raises(APIError) as error:
        await endpoint(people.app, "decide")(
            uid(), "merge", extra.Decision(target_id=uid() if target else None), db
        )
    assert error.value.code == "INVALID_TARGET"


async def test_internal_suggestion_query_filters(people, monkeypatch):
    paginated = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(extra, "paginate", paginated)
    await endpoint(people.app, "suggestions")(request(), "pending", "skills", 10, database())
    sql = str(paginated.call_args.args[1])
    assert "dictionary_suggestions.status" in sql and "dictionary_suggestions.kind" in sql


@pytest.mark.parametrize("actor", [None, "viewer"])
async def test_resource_projection_excludes_contacts(people, actor):
    db = database()
    row = profile(contacts=[{"visibility": "public", "type": "email", "value": "private contact"}])
    db.scalar.return_value = row
    result = await endpoint(people.app, "read_resource")(
        row.user_id, extra.ReadInput(actor_id=uid() if actor else None), request(), db
    )
    assert "contacts" not in result and result["id"] == row.user_id


async def test_unmatched_events_and_missing_rows_are_noops():
    db = database()
    await events.delete_user(None, db, "missing")
    await events.handle(
        None, db, {"event_type": "identity.user_blocked", "payload": {"user_id": "missing"}}
    )
    await events.handle(
        None,
        db,
        {
            "event_type": "projects.project_updated",
            "payload": {"status": "completed", "member_ids": ["missing"], "project_id": "p"},
        },
    )
    await events.handle(
        None, db, {"event_type": "projects.project_updated", "payload": {"status": "active"}}
    )
    db.add.assert_not_called()


async def test_profile_references_and_avatar_are_bound_and_deduplicated(people, monkeypatch):
    db, row = database(), profile()
    university, faculty, department, skill, interest, avatar = [uid() for _ in range(6)]
    db.scalar.return_value = row
    db.get.side_effect = [
        Obj(active=True, parent_id=university),
        Obj(active=True, parent_id=faculty),
    ]
    validate = AsyncMock(side_effect=lambda db, identifier, kind: {"id": identifier, "kind": kind})
    monkeypatch.setattr(people, "validate_reference", validate)
    call = AsyncMock()
    monkeypatch.setattr(people, "internal_call", call)
    body = people.ProfilePatch(
        version=1,
        university_id=university,
        faculty_id=faculty,
        department_id=department,
        specialty_id=uid(),
        city_id=uid(),
        skill_ids=[skill, skill],
        interest_ids=[interest, interest],
        avatar_file_id=avatar,
    )
    result = await people.edit_profile(body, request(), {"id": row.user_id}, db)
    assert result["avatar_file_id"] == avatar and len(row.skills) == len(row.interests) == 1
    assert validate.await_count == 7
    assert call.call_args.kwargs["json"]["resource_id"] == row.user_id


async def test_reference_lookup_and_internal_batch_contracts(people):
    db, row = database(), profile()
    entry = DictionaryEntry(
        id=uid(), name="Python", kind="skills", skill_kind="technology", active=True
    )
    db.scalar.return_value = entry
    assert await people.validate_reference(db, entry.id, "skills") == {
        "id": entry.id,
        "name": "Python",
        "kind": "skills",
        "skill_kind": "technology",
    }
    db.scalars.return_value = rows([row])
    result = await people.batch_profiles(people.BatchInput(ids=[row.user_id]), db)
    assert result["items"][0]["id"] == row.user_id
    db.scalars.return_value = rows([entry])
    assert (await people.validate_dictionary(people.BatchInput(ids=[entry.id, entry.id]), db))[
        "items"
    ][0]["id"] == entry.id
    db.scalar.return_value = row
    assert "contacts" not in await people.internal_profile(row.user_id, db)


async def test_avatar_proxy_sets_no_store_and_uses_profile_binding(people, monkeypatch):
    db, row, response = database(), profile(avatar_file_id=uid()), Response()
    db.scalar.return_value = row
    call = AsyncMock(return_value={"url": "https://files.invalid/avatar"})
    monkeypatch.setattr(people, "internal_call", call)
    result = await people.avatar_url(row.user_id, request(), response, db)
    assert result["url"].endswith("avatar") and response.headers["Cache-Control"] == "no-store"
    assert call.call_args.kwargs["json"]["resource_type"] == "profile"


async def test_user_profile_does_not_disclose_blocked_or_missing_account(people):
    db = database()
    with pytest.raises(APIError) as error:
        await people.user_profile(uid(), request(), None, db)
    assert error.value.status == 404


async def test_suggest_dictionary_records_proposer(people):
    db, user = database(), {"id": uid()}
    result = await people.suggest(
        people.SuggestionInput(kind="skills", name="New technology"), request(), user, db
    )
    assert result["user_id"] == user["id"] and result["name"] == "New technology"
