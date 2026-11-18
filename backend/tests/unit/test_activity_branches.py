from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from libs.platform.database import now, uid
from libs.platform.errors import APIError
from services.activity import events
from services.activity.models import ModerationAction, Report
from tests.unit.support import database, operation, request, rows, service

pytestmark = pytest.mark.unit


@pytest.fixture
def activity(monkeypatch):
    module = service(monkeypatch, "activity")
    monkeypatch.setattr(module, "command", operation)
    monkeypatch.setattr(module, "append_event", AsyncMock())
    return module


@pytest.mark.parametrize("created", [False, True])
async def test_duplicate_favorites_do_not_emit_duplicate_deltas(activity, monkeypatch, created):
    db = database()
    db.scalar.return_value = "saved-id" if created else None
    monkeypatch.setattr(activity, "resource", AsyncMock(return_value={"id": "target"}))
    await activity.add_favorite("paper", "target", request(), {"id": "u"}, db)
    await activity.remove_favorite("paper", "target", {"id": "u"}, db)
    assert activity.append_event.await_count == (2 if created else 0)
    if created:
        assert [call.args[-1]["delta"] for call in activity.append_event.call_args_list] == [1, -1]


@pytest.mark.parametrize("endpoint", ["favorites", "notifications"])
@pytest.mark.parametrize("error_status", [None, 403, 404, 503, 422])
async def test_saved_cards_and_notifications_degrade_without_leaking_hidden_content(
    activity, monkeypatch, endpoint, error_status
):
    card = {"id": "visible"}
    resource = AsyncMock(
        return_value=card,
        side_effect=APIError(error_status, "UPSTREAM", "error") if error_status else None,
    )
    monkeypatch.setattr(activity, "resource", resource)
    item = {
        "target_id": "target",
        "created_at": now(),
        "target_type": "paper",
        "title": "Secret title",
        "body": "Secret text",
    }
    paginate = AsyncMock(return_value={"items": [item]})
    monkeypatch.setattr(activity, "paginate", paginate)

    async def call():
        if endpoint == "favorites":
            return await activity.favorites("paper", request(), 10, {"id": "u"}, database())
        return await activity.notifications(request(), True, 10, {"id": "u"}, database())

    if error_status == 422:
        with pytest.raises(APIError) as error:
            await call()
        assert error.value.status == 422
    else:
        result = (await call())["items"][0]
        expected = (
            "unavailable"
            if error_status in {403, 404}
            else "temporarily_unavailable"
            if error_status
            else "available"
        )
        if endpoint == "favorites":
            assert result["availability"] == expected
            assert result["card"] == (None if error_status else card)
        else:
            assert result["action"] == ("view" if expected == "available" else expected)
            if error_status in {403, 404}:
                assert result["title"] != "Secret title" and result["body"] == ""


@pytest.mark.parametrize("status", [None, "pending", "succeeded"])
@pytest.mark.parametrize("failure", [None, 422, 503])
async def test_async_moderation_retries_only_temporary_errors(
    activity, monkeypatch, status, failure
):
    db = database()
    row = Obj(id="a", status=status, result=None) if status else None
    db.scalar.return_value = row
    perform = AsyncMock(
        return_value={"done": True},
        side_effect=APIError(failure, "DENIED", "denied") if failure else None,
    )
    monkeypatch.setattr(activity, "perform_action", perform)
    event = {"event_type": "activity.action_requested", "payload": {"action_id": "a"}}
    if status == "pending" and failure == 503:
        with pytest.raises(APIError):
            await events.handle(None, db, event)
        assert row.status == "pending"
    else:
        await events.handle(None, db, event)
        if status == "pending":
            assert row.status == ("failed" if failure else "succeeded")
            assert row.result == ({"code": "DENIED"} if failure else {"done": True})
        else:
            perform.assert_not_awaited()


async def test_reconcile_enqueues_pending_actions_and_delete_emits_favorite_removals(monkeypatch):
    db = database()
    emit = AsyncMock()
    monkeypatch.setattr(events, "append_event", emit)
    db.scalars.return_value = rows([Obj(id="a")])
    await events.reconcile(None, db)
    assert emit.call_args.args[1] == "activity.action_requested"
    db.scalars.return_value = rows([Obj(target_id="p", target_type="paper")])
    await events.delete_user(None, db, "u")
    assert emit.call_args.args[-1] == {"type": "paper", "target_id": "p", "delta": -1}
    db.delete.assert_awaited_once()
    db.execute.assert_awaited_once()


@pytest.mark.parametrize("failure", [None, 403, 503])
async def test_moderation_request_records_synchronous_result_or_pending_retry(
    activity, monkeypatch, failure
):
    db = database()

    async def flush():
        db.add.call_args.args[0].status = "pending"

    db.flush.side_effect = flush
    monkeypatch.setattr(
        activity,
        "perform_action",
        AsyncMock(
            return_value={"ok": True},
            side_effect=APIError(failure, "DENIED", "denied") if failure else None,
        ),
    )
    result = await activity.moderate_resource(
        uid(),
        "restore",
        activity.ModerationInput(reason="restored after review"),
        request(path="/api/v1/admin/papers/id/restore"),
        {"id": uid(), "roles": ["admin"]},
        db,
    )
    assert result["status"] == (
        "pending" if failure == 503 else "failed" if failure else "succeeded"
    )
    assert db.add.call_count == 2 and activity.append_event.await_count == 1


async def test_report_and_admin_queries_cover_filters_and_details(activity, monkeypatch):
    db = database()
    user = {"id": uid(), "roles": ["admin"]}
    paginate = AsyncMock(return_value={"items": []})
    monkeypatch.setattr(activity, "paginate", paginate)
    await activity.reports(request(), "resolved", "paper", 10, user, db)
    assert "reports.status" in str(paginate.call_args.args[1])
    await activity.audit(request(), 10, "actor", "target", user, db)
    assert "audit_log.actor_id" in str(paginate.call_args.args[1]) and "audit_log.target_id" in str(
        paginate.call_args.args[1]
    )
    report = Report(
        id=uid(),
        version=1,
        user_id=user["id"],
        target_type="paper",
        target_id=uid(),
        reason_code="spam",
        description="report text",
    )
    db.get.return_value = report
    assert (await activity.report_detail(report.id, user, db))["id"] == report.id
    db.scalar.return_value = report
    result = await activity.decide_report(
        report.id,
        activity.ReportDecision(version=1, status="resolved", resolution="resolved by moderator"),
        user,
        db,
    )
    assert result["status"] == "resolved" and result["version"] == 2
    monkeypatch.setattr(activity, "internal_call", AsyncMock(return_value={"id": "target"}))
    assert await activity.admin_user("target", request(), user) == {"id": "target"}


@pytest.mark.parametrize(
    "kind,operation_name",
    [("user", "block"), ("user", "unblock"), ("project", "hide"), ("paper", "restore")],
)
async def test_moderation_dispatch_has_explicit_actor_and_idempotent_action(
    activity, monkeypatch, kind, operation_name
):
    call = AsyncMock(return_value={"status": "succeeded"})
    monkeypatch.setattr(activity, "internal_call", call)
    row = ModerationAction(
        id="action",
        actor_id="actor",
        target_id="target",
        target_type=kind,
        operation=operation_name,
        reason="reason",
    )
    await activity.perform_action(request(), row)
    payload = call.call_args.kwargs["json"]
    assert payload["actor_id"] == "actor" and payload["action_id"] == "action"
    assert payload["blocked" if kind == "user" else "hidden"] is (
        operation_name in {"block", "hide"}
    )


async def test_count_batch_and_read_notification_endpoints(activity):
    db = database()
    db.scalars.return_value = rows(["saved"])
    assert await activity.favorite_batch(
        activity.FavoriteBatch(user_id=uid(), type="paper", ids=[uid()]), db
    ) == {"saved_ids": ["saved"]}
    assert await activity.paper_favorite_count("paper", db) == {"count": 0}
    assert await activity.unread_count({"id": "u"}, db) == {"count": 0}
    original = now()
    row = Obj(user_id="u", read_at=original)
    db.get.return_value = row
    await activity.read_notification("n", {"id": "u"}, db)
    assert row.read_at is original


async def test_non_resource_notification_does_not_make_unnecessary_authority_request(
    activity, monkeypatch
):
    pagination = AsyncMock(
        return_value={
            "items": [
                {"target_type": "invitation", "target_id": "i", "title": "Invite", "body": ""}
            ]
        }
    )
    monkeypatch.setattr(activity, "paginate", pagination)
    resource = AsyncMock()
    monkeypatch.setattr(activity, "resource", resource)
    result = await activity.notifications(request(), False, 10, {"id": "u"}, database())
    assert result["items"][0]["action"] == "view_invitation"
    resource.assert_not_awaited()
    await activity.reports(
        request(), None, None, 10, {"id": "admin", "roles": ["admin"]}, database()
    )


async def test_report_creation_records_the_reporting_user(activity, monkeypatch):
    db, user = database(), {"id": uid()}
    monkeypatch.setattr(activity, "resource", AsyncMock())
    result = await activity.report(
        activity.ReportInput(
            target_type="paper",
            target_id=uid(),
            reason_code="plagiarism",
            description="Detailed report description",
        ),
        request(),
        user,
        db,
    )
    assert result["user_id"] == user["id"] and result["reason_code"] == "plagiarism"


async def test_action_status_reports_durable_result(activity):
    db = database()
    db.get.return_value = Obj(id="action", status="failed", result={"code": "FORBIDDEN"})
    assert await activity.action_status("action", {"id": "a", "roles": ["admin"]}, db) == {
        "action_id": "action",
        "status": "failed",
        "result": {"code": "FORBIDDEN"},
    }
