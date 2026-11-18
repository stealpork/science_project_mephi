from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from libs.platform.config import Settings
from services.identity import events
from tests.unit.support import database, request, rows

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("row", [None, Obj(status="completed")])
async def test_deletion_event_is_idempotent_for_absent_or_completed_operations(monkeypatch, row):
    db = database()
    db.scalar.return_value = row
    forward = AsyncMock()
    monkeypatch.setattr(events, "internal_call", forward)
    await events.handle(
        request(),
        db,
        {"event_type": "identity.account_deletion_requested", "payload": {"deletion_id": "d"}},
    )
    forward.assert_not_awaited()
    db.get.assert_not_awaited()


@pytest.mark.parametrize(
    "completed", [[], ["projects", "publications", "people", "media", "activity"]]
)
async def test_deletion_retry_preserves_completed_service_list_and_scrubs_credentials(
    monkeypatch, completed
):
    db = database()
    row = Obj(id="d", user_id="u", status="processing", completed_services=completed)
    user = Obj(id="u")
    db.scalar.return_value, db.get.return_value = row, user
    forward = AsyncMock()
    monkeypatch.setattr(events, "internal_call", forward)
    await events.handle(
        request(),
        db,
        {"event_type": "identity.account_deletion_requested", "payload": {"deletion_id": "d"}},
    )
    assert row.completed_services == ["projects", "publications", "people", "media", "activity"]
    assert user.status == "deleted"
    assert (
        row.status == "completed"
        and user.email == "u@deleted.invalid"
        and user.password_hash == "!deleted"
    )
    assert user.mfa_secret is None and user.recovery_hashes == [] and user.roles == []
    assert forward.await_count == 5


@pytest.mark.parametrize("pending", [[], [Obj(user_id="u", id="d")]])
async def test_reconciliation_requeues_incomplete_deletions(monkeypatch, pending):
    db = database()
    db.scalars.return_value = rows(pending)
    append = AsyncMock()
    monkeypatch.setattr(events, "append_event", append)
    await events.reconcile(request(), db)
    assert append.await_count == len(pending)
    if pending:
        append.assert_awaited_once_with(
            db, "identity.account_deletion_requested", "u", {"user_id": "u", "deletion_id": "d"}
        )


@pytest.mark.parametrize("kind", ["reset_password", "verify_email"])
@pytest.mark.parametrize("security", ["none", "tls", "starttls"])
async def test_transactional_email_uses_configured_transport_and_urlencoded_token(
    monkeypatch, kind, security
):
    send = AsyncMock()
    monkeypatch.setattr(events.aiosmtplib, "send", send)
    await events.handle(
        request(
            Settings(smtp_security=security, smtp_username="sender", smtp_password="credential")
        ),
        database(),
        {
            "event_type": "identity.mail_requested",
            "payload": {"kind": kind, "email": "recipient@example.org", "token": "a&b"},
        },
    )
    message = send.call_args.args[0]
    assert "token=a%26b" in message.get_content()
    assert send.call_args.kwargs["use_tls"] == (security == "tls")
    assert send.call_args.kwargs["start_tls"] == (security == "starttls")
