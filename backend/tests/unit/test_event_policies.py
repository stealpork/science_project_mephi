from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.platform.errors import APIError
from services.activity import events as activity
from services.people import events as people
from services.projects import events as projects
from services.publications import events as papers

pytestmark = pytest.mark.unit


def database():
    return Obj(
        scalar=AsyncMock(),
        scalars=AsyncMock(),
        execute=AsyncMock(),
        delete=AsyncMock(),
        add=MagicMock(),
    )


def rows(items):
    return Obj(all=lambda: items)


@pytest.mark.parametrize(
    "kind,payload,target",
    [
        ("projects.application_submitted", {"owner_id": "u"}, "project"),
        ("projects.application_decided", {"user_id": "u", "status": "accepted"}, "project"),
        ("projects.invitation_created", {"user_id": "u", "invitation_id": "i"}, "invitation"),
        ("projects.invitation_decided", {"owner_id": "u", "status": "declined"}, "project"),
        ("projects.member_joined", {"user_id": "u"}, "project"),
        ("projects.member_left", {"user_id": "u"}, "project"),
        ("projects.owner_changed", {"user_id": "u"}, "project"),
        ("projects.project_updated", {"member_ids": ["u", "u", None]}, "project"),
        ("projects.project_moderated", {"user_id": "u"}, "project"),
        ("publications.paper_hidden", {"user_id": "u", "paper_id": "p"}, "paper"),
        ("people.dictionary_decided", {"user_id": "u", "suggestion_id": "s"}, "suggestion"),
        ("media.file_rejected", {"user_id": "u", "file_id": "f"}, "upload"),
    ],
)
async def test_notification_routing_deduplicates_recipients(kind, payload, target):
    db = database()
    await activity.handle(
        None,
        db,
        {"event_id": "event", "event_type": kind, "payload": {"project_id": "project", **payload}},
    )
    db.add.assert_called_once()
    notification = db.add.call_args.args[0]
    assert notification.user_id == "u" and notification.target_type == target
    assert notification.event_id == "event" and notification.title


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("projects.owner_changed", {"user_id": None}),
        ("projects.project_updated", {"member_ids": []}),
        ("publications.paper_changed", {}),
    ],
)
async def test_unaddressed_events_do_not_create_notifications(kind, payload):
    db = database()
    await activity.handle(None, db, {"event_id": "e", "event_type": kind, "payload": payload})
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "status,expected", [("active", False), ("blocked", True), ("deleting", None)]
)
async def test_delayed_registration_respects_current_account_state(monkeypatch, status, expected):
    db = database()
    db.scalar.return_value = None
    monkeypatch.setattr(people, "internal_call", AsyncMock(return_value={"status": status}))
    await people.handle(
        None,
        db,
        {
            "event_type": "identity.user_registered",
            "payload": {"user_id": "u", "first_name": "Имя", "last_name": "Фамилия"},
        },
    )
    if expected is None:
        db.add.assert_not_called()
    else:
        assert db.add.call_args.args[0].blocked is expected


async def test_duplicate_registration_does_not_resurrect_deleted_profile(monkeypatch):
    db = database()
    db.scalar.return_value = Obj(deleted=True)
    call = AsyncMock()
    monkeypatch.setattr(people, "internal_call", call)
    await people.handle(
        None, db, {"event_type": "identity.user_registered", "payload": {"user_id": "u"}}
    )
    call.assert_not_awaited()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "status,blocked", [("active", False), ("blocked", True), ("deleted", True)]
)
async def test_reordered_block_events_read_authority(monkeypatch, status, blocked):
    db = database()
    profile = Obj(blocked=not blocked)
    db.scalar.return_value = profile
    monkeypatch.setattr(people, "internal_call", AsyncMock(return_value={"status": status}))
    await people.handle(
        None, db, {"event_type": "identity.user_blocked", "payload": {"user_id": "u"}}
    )
    assert profile.blocked is blocked


@pytest.mark.parametrize("existing", [True, False])
async def test_completed_project_experience_is_deduplicated(existing):
    db = database()
    profile = Obj(experience=[{"project_id": "p"}] if existing else [])
    db.scalar.return_value = profile
    await people.handle(
        None,
        db,
        {
            "event_type": "projects.project_updated",
            "payload": {
                "status": "completed",
                "project_id": "p",
                "title": "Проект",
                "member_ids": ["u"],
                "access_type": "private_invite",
            },
        },
    )
    assert len(profile.experience) == 1
    if not existing:
        assert profile.experience[0]["source"] == "platform"
        assert profile.experience[0]["private"] is True


@pytest.mark.parametrize("error_status", [None, 403, 404, 503])
async def test_paper_successor_must_be_active_and_have_private_project_access(
    monkeypatch, error_status
):
    db = database()
    paper = Obj(id="p", owner_id="deleted", project_id="private", status="draft")
    blocked, allowed = Obj(user_id="blocked"), Obj(user_id="allowed")
    deleted_author = Obj(user_id="deleted", access_granted_at="old", display_name="Автор")
    db.scalars.side_effect = [rows([paper]), rows([blocked, allowed]), rows([deleted_author])]

    async def call(request, service, method, path, **kwargs):
        if service == "identity":
            return {"status": "blocked" if path.endswith("blocked") else "active"}
        if error_status:
            raise APIError(error_status, "FAILED", "error")
        return {"access_type": "private_invite"}

    monkeypatch.setattr(papers, "internal_call", call)
    if error_status == 503:
        with pytest.raises(APIError):
            await papers.delete_user(None, db, "deleted")
    else:
        await papers.delete_user(None, db, "deleted")
        assert paper.owner_id == ("allowed" if error_status is None else None)
        assert paper.status == "draft"
        assert deleted_author.user_id is None and deleted_author.access_granted_at is None
        assert "аккаунт удалён" in deleted_author.display_name


@pytest.mark.parametrize("count", [0, 7, 2])
async def test_favorite_projection_uses_authoritative_count_not_event_delta(monkeypatch, count):
    db = database()
    paper = Obj(id="p", saves_count=99)
    db.scalar.return_value = paper
    monkeypatch.setattr(papers, "internal_call", AsyncMock(return_value={"count": count}))
    await papers.handle(None, db, {"payload": {"type": "paper", "target_id": "p", "delta": -1}})
    assert paper.saves_count == count


async def test_nonpaper_favorite_event_is_ignored():
    db = database()
    await papers.handle(None, db, {"payload": {"type": "project"}})
    db.scalar.assert_not_awaited()


@pytest.mark.parametrize("successor", [True, False])
async def test_project_deletion_preserves_state_and_skips_blocked_successor(monkeypatch, successor):
    db = database()
    project = Obj(id="p", owner_id="old", status="archived", access_type="private_invite")
    candidate = Obj(user_id="next", role="researcher")
    db.scalars.side_effect = [
        rows([project]),
        rows([Obj(user_id="blocked"), candidate] if successor else []),
    ]
    monkeypatch.setattr(
        projects,
        "internal_call",
        AsyncMock(side_effect=[{"status": "blocked"}, {"status": "active"}]),
    )
    event = AsyncMock()
    monkeypatch.setattr(projects, "append_event", event)
    await projects.delete_user(None, db, "old")
    assert project.owner_id == ("next" if successor else None)
    assert project.status == "archived" and project.access_type == "private_invite"
    assert (candidate.role == "leader") is successor
    event.assert_awaited_once()


async def test_account_deletion_removes_personal_profile_data():
    db = database()
    profile = Obj(
        deleted=False,
        blocked=False,
        first_name="A",
        last_name="B",
        bio="private",
        contacts=[{}],
        skills=[{}],
        interests=[{}],
        experience=[{}],
        avatar_file_id="f",
    )
    db.scalar.return_value = profile
    await people.delete_user(None, db, "u")
    assert profile.deleted and profile.blocked and profile.avatar_file_id is None
    assert not any(
        [profile.bio, profile.contacts, profile.skills, profile.interests, profile.experience]
    )
