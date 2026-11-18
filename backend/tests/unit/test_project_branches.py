from datetime import timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from libs.platform.database import now, uid
from libs.platform.errors import APIError
from services.projects.models import Application, Invitation, Member, Project
from tests.unit.support import database, operation, request, rows, service

pytestmark = pytest.mark.unit


@pytest.fixture
def projects(monkeypatch):
    module = service(monkeypatch, "projects")
    monkeypatch.setattr(module, "command", operation)
    monkeypatch.setattr(module, "append_event", AsyncMock())
    return module


def project(**changes):
    return Project(
        **{
            "id": uid(),
            "owner_id": uid(),
            "creator_id": uid(),
            "title": "Research",
            "description": "Description",
            "research_goal": "Goal",
            "area_id": uid(),
            "access_type": "public_open",
            "status": "recruiting",
            "team_size": 5,
            "version": 1,
            "moderation_status": "visible",
            "required_skill_ids": [],
            "interest_ids": [],
            "eligible_courses": [],
            **changes,
        }
    )


@pytest.mark.parametrize(
    "reason",
    [
        None,
        "AUTH_REQUIRED",
        "ALREADY_MEMBER",
        "OWNER_UNAVAILABLE",
        "APPLICATION_PENDING",
        "INVITE_ONLY",
        "RECRUITMENT_CLOSED",
        "PROJECT_FULL",
    ],
)
async def test_project_card_explains_every_apply_denial(projects, reason):
    db, row, user = database(), project(), {"id": uid()}
    if reason == "AUTH_REQUIRED":
        user = None
    if reason == "OWNER_UNAVAILABLE":
        row.owner_id = None
    if reason == "INVITE_ONLY":
        row.access_type = "public_invite"
    if reason == "RECRUITMENT_CLOSED":
        row.status = "active"
    facts = {
        "counts": {row.id: 5 if reason == "PROJECT_FULL" else 1},
        "members": {row.id: Obj(role="researcher")} if reason == "ALREADY_MEMBER" else {},
        "applications": {row.id: Obj(status="pending")} if reason == "APPLICATION_PENDING" else {},
        "invitations": {row.id: Obj(status="pending")},
    }
    result = await projects.project_view(db, row, user, facts)
    assert result["viewer"]["cannot_apply_reason"] == reason
    assert result["viewer"]["can_apply"] is (reason is None)
    assert result["viewer"]["invitation_status"] == "pending"
    db.scalar.assert_not_awaited()


async def test_project_cards_batch_counts_and_viewer_relations(projects):
    db, row, user = database(), project(), {"id": uid()}
    db.execute.return_value = rows([(row.id, 2)])
    db.scalars.side_effect = [rows([Obj(project_id=row.id, role="analyst")]), rows([]), rows([])]
    result = await projects.project_cards(db, [row], user)
    assert result[0]["members_count"] == 2 and result[0]["viewer"]["is_member"]
    assert db.scalars.await_count == 3 and db.execute.await_count == 1
    assert await projects.project_cards(db, [], user) == []


@pytest.mark.parametrize("skills_mode", ["all", "any"])
async def test_project_catalog_applies_all_filters(projects, monkeypatch, skills_mode):
    db = database()
    pagination = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(projects, "paginate", pagination)

    async def enrich(req, kind, user, cards):
        return cards

    monkeypatch.setattr(projects, "enrich_favorites", enrich)
    query = (
        "area_id=a&university_id=u&status=recruiting&access_type=public_open&owner_id=o&member_id=m&skill_id=s&skill_id=s2&skills_mode="
        + skills_mode
        + "&created_from=2026-01-01T00:00:00Z&created_to=2026-12-31T00:00:00Z&team_size_min=2&team_size_max=9&has_free_slots=true"
    )
    result = await projects.projects(request(query=query), "research", 10, None, db)
    assert result["items"] == []
    sql = str(pagination.call_args.args[1])
    for field in [
        "area_id",
        "university_id",
        "status",
        "access_type",
        "owner_id",
        "required_skill_ids",
        "created_at",
        "team_size",
    ]:
        assert "projects." + field in sql
    assert "project_members.user_id" in sql


@pytest.mark.parametrize(
    "query,code",
    [
        ("created_from=2026-01-01", "INVALID_DATE"),
        ("created_to=garbled", "INVALID_DATE"),
        ("team_size_min=garbled", "INVALID_FILTER"),
    ],
)
async def test_catalog_rejects_bad_dates_and_numeric_filters(projects, query, code):
    with pytest.raises(APIError) as error:
        await projects.projects(request(query=query), "", 20, None, database())
    assert error.value.code == code


async def test_project_edit_updates_version_validates_team_and_visibility(projects, monkeypatch):
    row, db = project(access_type="private_invite"), database()
    monkeypatch.setattr(projects, "own_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "project_view", AsyncMock(return_value={"id": row.id}))
    monkeypatch.setattr(projects, "member_count", AsyncMock(return_value=2))
    await projects.edit_project(
        row.id,
        projects.ProjectPatch(
            version=1,
            title="New title",
            team_size=3,
            access_type="public_open",
            confirm_visibility_expansion=True,
        ),
        request(),
        {"id": row.owner_id},
        db,
    )
    assert (
        row.version == 2
        and row.title == "New title"
        and row.team_size == 3
        and row.access_type == "public_open"
    )
    projects.append_event.assert_awaited_once()


async def test_member_rejoining_reuses_historical_membership(projects, monkeypatch):
    row, db = project(), database()
    member = Member(id=uid(), project_id=row.id, user_id=uid(), role="researcher", left_at=now())
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=None))
    monkeypatch.setattr(projects, "member_count", AsyncMock(return_value=1))
    db.scalar.return_value = member
    assert await projects.add_member(db, row, member.user_id, "analyst") is member
    assert member.left_at is None and member.role == "analyst" and member.created_at
    db.add.assert_not_called()
    assert db.execute.await_count == 2


@pytest.mark.parametrize(
    "previous", [None, "pending", "rejected-recent", "rejected-old", "withdrawn"]
)
async def test_application_cooldown_and_pending_policy(projects, monkeypatch, previous):
    row, db, user = project(), database(), {"id": uid()}
    monkeypatch.setattr(projects, "get_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=None))
    monkeypatch.setattr(projects, "member_count", AsyncMock(return_value=1))
    db.scalar.return_value = (
        Obj(
            status="rejected" if previous and previous.startswith("rejected") else previous,
            decided_at=now() - timedelta(days=8 if previous == "rejected-old" else 1),
        )
        if previous
        else None
    )
    body = projects.ApplicationInput(message="I can contribute expertise")
    if previous in {"pending", "rejected-recent"}:
        with pytest.raises(APIError) as error:
            await projects.apply(row.id, body, request(), user, db)
        assert error.value.code == (
            "APPLICATION_PENDING" if previous == "pending" else "APPLICATION_COOLDOWN"
        )
        db.add.assert_not_called()
    else:
        result = await projects.apply(row.id, body, request(), user, db)
        assert result["user_id"] == user["id"] and db.add.call_count == 1


@pytest.mark.parametrize("action", ["accept", "reject", "withdraw"])
async def test_application_decisions_update_state_and_notify(projects, monkeypatch, action):
    db, row = database(), project()
    application = Application(
        id=uid(),
        project_id=row.id,
        user_id=uid(),
        status="pending",
        message="Contribution",
        requested_role="researcher",
    )
    db.get.return_value = application
    db.refresh = AsyncMock()
    monkeypatch.setattr(projects, "get_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "internal_call", AsyncMock(return_value={"status": "active"}))

    async def add(db, parent, user_id, role):
        application.status = "accepted"
        return Member(id=uid(), project_id=parent.id, user_id=user_id, role=role)

    monkeypatch.setattr(projects, "add_member", add)
    user = {"id": application.user_id if action == "withdraw" else row.owner_id}
    result = await projects.decide_application(
        row.id,
        application.id,
        action,
        request(),
        projects.DecisionInput(reason="decision reason"),
        user,
        db,
    )
    assert (
        result["application"]["status"]
        == {"accept": "accepted", "reject": "rejected", "withdraw": "withdrawn"}[action]
    )
    assert ("member" in result) is (action == "accept")
    projects.append_event.assert_awaited_once()


@pytest.mark.parametrize(
    "handler", ["list_applications", "my_applications", "my_invitations", "list_invitations"]
)
async def test_request_catalogs_apply_user_project_and_status_filters(
    projects, monkeypatch, handler
):
    paginate = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(projects, "paginate", paginate)
    monkeypatch.setattr(projects, "own_project", AsyncMock())
    db, req, user = database(), request(), {"id": "user"}
    if handler == "list_applications":
        result = await projects.list_applications("project", req, "pending", 10, user, db)
    elif handler == "list_invitations":
        result = await projects.list_invitations("project", req, 10, user, db)
    else:
        result = await getattr(projects, handler)(req, "pending", 10, user, db)
    assert result == {"items": []}
    sql = str(paginate.call_args.args[1])
    assert "project_id" in sql if handler.startswith("list") else "user_id" in sql


@pytest.mark.parametrize("action", ["accept", "decline", "revoke"])
async def test_invitation_decisions_enforce_narrow_actor_right(projects, monkeypatch, action):
    db, row = database(), project(access_type="private_invite")
    invitation = Invitation(
        id=uid(),
        project_id=row.id,
        user_id=uid(),
        status="pending",
        role="analyst",
        expires_at=now() + timedelta(days=1),
    )
    db.get.return_value, db.scalar.return_value = invitation, row
    db.refresh = AsyncMock()

    async def add(*args):
        invitation.status = "accepted"

    monkeypatch.setattr(projects, "add_member", add)
    user = {"id": row.owner_id if action == "revoke" else invitation.user_id}
    result = await projects.decide_invitation(row.id, invitation.id, action, request(), user, db)
    assert (
        result["status"]
        == {"accept": "accepted", "decline": "declined", "revoke": "revoked"}[action]
    )
    projects.append_event.assert_awaited_once()


@pytest.mark.parametrize(
    "state", ["pending", "expired", "declined-member", "declined-outsider", "hidden", "missing"]
)
async def test_invitation_preview_does_not_grant_full_private_access(projects, monkeypatch, state):
    db, row = database(), project(access_type="private_invite")
    if state == "hidden":
        row.moderation_status = "hidden"
    db.get.return_value = None if state == "missing" else row
    monkeypatch.setattr(
        projects,
        "membership",
        AsyncMock(return_value=Obj() if state == "declined-member" else None),
    )
    item = {
        "status": "declined" if state.startswith("declined") else "pending",
        "expires_at": (now() + timedelta(days=-1 if state == "expired" else 1)).isoformat(),
        "project_id": row.id,
    }
    monkeypatch.setattr(projects, "paginate", AsyncMock(return_value={"items": [item]}))
    result = (await projects.my_invitations(request(), None, 10, {"id": "u"}, db))["items"][0]
    assert (result["project"] is not None) is (state in {"pending", "declined-member"})
    if state == "expired":
        assert result["status"] == "expired"


async def test_member_role_changes_and_kick_wrapper(projects, monkeypatch):
    row, db = project(), database()
    member = Member(id=uid(), project_id=row.id, user_id=uid(), role="researcher")
    monkeypatch.setattr(projects, "own_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=member))
    result = await projects.edit_member(
        row.id, member.user_id, projects.RoleInput(role="analyst"), {"id": row.owner_id}, db
    )
    assert result["role"] == "analyst"
    await projects.kick(row.id, member.user_id, {"id": row.owner_id}, db)
    assert member.left_at


@pytest.mark.parametrize("personalized", [False, True])
async def test_recommendations_skip_full_projects_and_explain_fallback(
    projects, monkeypatch, personalized
):
    db, first, full = database(), project(), project()
    db.scalars.return_value = rows([first, full])
    monkeypatch.setattr(
        projects,
        "internal_call",
        AsyncMock(return_value={"skills": [{"id": "s"}] if personalized else []}),
    )
    monkeypatch.setattr(
        projects,
        "project_cards",
        AsyncMock(
            return_value=[{"id": first.id, "free_slots": 1}, {"id": full.id, "free_slots": 0}]
        ),
    )
    monkeypatch.setattr(projects, "enrich_favorites", AsyncMock())
    result = await projects.recommended_projects(request(), 20, {"id": "u"}, db)
    assert len(result["items"]) == 1 and result["items"][0]["project"]["id"] == first.id
    assert result["personalized"] is personalized
    assert result["fallback_reason"] == (None if personalized else "profile_incomplete")


async def test_internal_read_and_link_paper_access(projects, monkeypatch):
    row, db = project(), database()
    monkeypatch.setattr(projects, "get_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=Obj(role="leader")))
    result = await projects.internal_access(
        row.id, projects.AccessCheck(actor_id=row.owner_id, action="link_paper"), db
    )
    assert result["can_read"] and result["is_member"]
    result = await projects.internal_access(row.id, projects.AccessCheck(), db)
    assert result["can_read"] and not result["is_member"]


async def test_owner_lookup_and_creation_fallback_name(projects, monkeypatch):
    db, row = database(), project()
    db.scalar.return_value = row
    assert await projects.get_project(db, row.id, None) is row
    assert await projects.own_project(db, row.id, {"id": row.owner_id}) is row
    monkeypatch.setattr(projects, "internal_call", AsyncMock(return_value={}))
    monkeypatch.setattr(projects, "project_view", AsyncMock(return_value={"created": True}))
    await projects.create_project(
        projects.ProjectInput(title="New research"), request(), {"id": row.owner_id}, db
    )
    created = db.add.call_args_list[0].args[0]
    assert created.creator_name == "Исследователь" and created.owner_id == row.owner_id


@pytest.mark.parametrize(
    "target,source",
    [
        ("recruiting", "draft"),
        ("recruiting", "active"),
        ("active", "recruiting"),
        ("completed", "active"),
        ("archived", "completed"),
    ],
)
async def test_transitions_close_pending_requests_and_emit_complete_team_context(
    projects, monkeypatch, target, source
):
    db, row = database(), project(status=source)
    monkeypatch.setattr(projects, "own_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "member_count", AsyncMock(return_value=1))
    monkeypatch.setattr(projects, "project_view", AsyncMock(return_value={"id": row.id}))
    db.scalars.return_value = rows(["leader", "member"])
    await projects.transition(row.id, target, request(), {"id": row.owner_id}, db)
    assert row.status == target and row.version == 2
    assert db.execute.await_count == (0 if target == "recruiting" else 2)
    payload = projects.append_event.call_args.args[-1]
    assert payload["member_ids"] == ["leader", "member"] and payload["status"] == target


@pytest.mark.parametrize("ownership", ["owner", "member", "all"])
async def test_my_project_catalog_relation_filters(projects, monkeypatch, ownership):
    pagination = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(projects, "paginate", pagination)
    monkeypatch.setattr(projects, "enrich_favorites", AsyncMock(return_value=[]))
    assert await projects.own_projects(
        request(), ownership, "active", 10, {"id": "viewer"}, database()
    ) == {"items": []}
    assert "projects.status" in str(pagination.call_args.args[1])


async def test_project_edit_with_omitted_team_size_preserves_capacity(projects, monkeypatch):
    row, db = project(), database()
    monkeypatch.setattr(projects, "own_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "project_view", AsyncMock(return_value={"id": row.id}))
    count = AsyncMock()
    monkeypatch.setattr(projects, "member_count", count)
    await projects.edit_project(
        row.id,
        projects.ProjectPatch(version=1, summary="New summary"),
        request(),
        {"id": row.owner_id},
        db,
    )
    assert row.team_size == 5 and row.summary == "New summary"
    count.assert_not_awaited()


async def test_anonymous_project_cards_need_no_viewer_queries(projects):
    db, row = database(), project()
    db.execute.return_value = rows([])
    result = await projects.project_cards(db, [row], None)
    assert result[0]["viewer"]["cannot_apply_reason"] == "AUTH_REQUIRED"
    db.scalars.assert_not_awaited()


@pytest.mark.parametrize("expires", [None, "custom"])
async def test_invitation_creation_expires_previous_pending_and_emits_new_id(
    projects, monkeypatch, expires
):
    db, row = database(), project()
    monkeypatch.setattr(projects, "own_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=None))
    monkeypatch.setattr(projects, "internal_call", AsyncMock(return_value={"status": "active"}))
    expiry = now() + timedelta(days=2) if expires else None
    body = projects.InvitationInput(user_id=uid(), expires_at=expiry)
    result = await projects.invite(row.id, body, request(), {"id": row.owner_id}, db)
    assert result["user_id"] == body.user_id and db.execute.await_count == 1
    assert projects.append_event.call_args.args[-1]["invitation_id"] == result["id"]


async def test_internal_access_checks_upload_membership(projects, monkeypatch):
    db, row = database(), project()
    monkeypatch.setattr(projects, "get_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=Obj(role="researcher")))
    result = await projects.internal_access(
        row.id, projects.AccessCheck(actor_id=uid(), action="upload"), db
    )
    assert result["is_member"] and result["can_read"]


async def test_manual_ownership_transfer_demotes_previous_leader(projects, monkeypatch):
    row, db = project(), database()
    previous = Member(user_id=row.owner_id, role="leader")
    successor = Member(user_id=uid(), role="analyst")
    monkeypatch.setattr(projects, "own_project", AsyncMock(return_value=row))
    monkeypatch.setattr(projects, "membership", AsyncMock(side_effect=[successor, previous]))
    monkeypatch.setattr(projects, "internal_call", AsyncMock(return_value={"status": "active"}))
    monkeypatch.setattr(projects, "project_view", AsyncMock(return_value={"id": row.id}))
    await projects.transfer(
        row.id,
        projects.TransferInput(user_id=successor.user_id),
        request(),
        {"id": previous.user_id},
        db,
    )
    assert (
        row.owner_id == successor.user_id
        and successor.role == "leader"
        and previous.role == "researcher"
    )


async def test_members_and_readable_batch_have_documented_identifiers(projects, monkeypatch):
    db, row, identifier = database(), project(), uid()
    monkeypatch.setattr(projects, "get_project", AsyncMock(return_value=row))
    db.scalars.return_value = rows(
        [Member(id=uid(), project_id=row.id, user_id=identifier, role="analyst", created_at=now())]
    )
    assert (await projects.members(row.id, None, db))["items"][0]["user_id"] == identifier
    db.scalars.return_value = rows([row])
    result = await projects.batch_readable(projects.BatchRead(ids=[row.id]), db)
    assert result["items"] == [{"id": row.id, "title": row.title, "access_type": row.access_type}]


async def test_first_join_creates_membership_and_closes_pending_requests(projects, monkeypatch):
    row, db, member_id = project(), database(), uid()
    monkeypatch.setattr(projects, "membership", AsyncMock(return_value=None))
    monkeypatch.setattr(projects, "member_count", AsyncMock(return_value=1))
    result = await projects.add_member(db, row, member_id, "analyst")
    assert result.user_id == member_id and result.role == "analyst"
    db.add.assert_called_once_with(result)
    assert db.execute.await_count == 2


async def test_application_catalog_without_status_keeps_all_decisions(projects, monkeypatch):
    pagination = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(projects, "paginate", pagination)
    monkeypatch.setattr(projects, "own_project", AsyncMock())
    await projects.list_applications("project", request(), None, 10, {"id": "owner"}, database())
    assert "project_applications.status =" not in str(pagination.call_args.args[1])
