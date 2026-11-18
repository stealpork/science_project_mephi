import json
from contextlib import asynccontextmanager
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.platform.database import metadata_for, now, uid
from libs.platform.worker import consume_one, publish_pending

pytestmark = pytest.mark.unit


def context(value):
    @asynccontextmanager
    async def manager():
        yield value

    return manager()


def database():
    session = Obj(scalar=AsyncMock(return_value=uid()), execute=AsyncMock(), info={})
    session.begin = lambda: context(None)
    return Obj(sessions=lambda: context(session), metadata=metadata_for("identity")), session


def message(event, attempts=0):
    return Obj(
        body=json.dumps(event).encode(),
        message_id=event.get("event_id", uid()),
        headers={"attempts": attempts},
        ack=AsyncMock(),
        nack=AsyncMock(),
    )


def event():
    return {
        "event_id": uid(),
        "schema_version": 1,
        "event_type": "test",
        "trace_id": "trace",
        "payload": {},
    }


async def test_duplicate_inbox_event_is_acknowledged_without_handler():
    db, session = database()
    session.scalar.return_value = None
    incoming = message(event())
    handler = AsyncMock()
    await consume_one(incoming, db, Obj(), handler, Obj(), Obj(), "identity")
    handler.assert_not_awaited()
    incoming.ack.assert_awaited_once()
    incoming.nack.assert_not_awaited()


async def test_handler_gets_isolated_trace_context():
    db, session = database()
    original = Obj(app=Obj(), state=Obj(request_id="unrelated"))
    handler = AsyncMock()
    incoming = message(event())
    await consume_one(incoming, db, original, handler, Obj(), Obj(), "identity")
    assert handler.call_args.args[0].state.request_id == "trace"
    assert original.state.request_id == "unrelated" and session.info["trace_id"] == "trace"
    incoming.ack.assert_awaited_once()


@pytest.mark.parametrize(
    "attempts,dead_letter",
    [
        (0, False),
        (8, False),
        (9, True),
        (100, True),
        ("corrupt", True),
        (None, True),
        (-100, False),
    ],
)
async def test_retry_and_dlq_are_confirmed_before_ack(attempts, dead_letter):
    db, _ = database()
    incoming = message(event(), attempts)
    channel = Obj(default_exchange=Obj(publish=AsyncMock()))
    dead = Obj(publish=AsyncMock())
    await consume_one(
        incoming,
        db,
        Obj(app=Obj()),
        AsyncMock(side_effect=RuntimeError("failed")),
        channel,
        dead,
        "identity",
    )
    selected = dead.publish if dead_letter else channel.default_exchange.publish
    selected.assert_awaited_once()
    forwarded = selected.call_args.args[0]
    assert forwarded.message_id == incoming.message_id
    assert forwarded.body == incoming.body and 1 <= forwarded.headers["attempts"] <= 10
    incoming.ack.assert_awaited_once()


@pytest.mark.parametrize("dead_letter", [False, True])
async def test_failed_retry_publication_nacks_original_without_loss(dead_letter):
    db, _ = database()
    incoming = message(event(), 9 if dead_letter else 0)
    failing = AsyncMock(side_effect=OSError("broker offline"))
    await consume_one(
        incoming,
        db,
        Obj(app=Obj()),
        AsyncMock(side_effect=ValueError()),
        Obj(default_exchange=Obj(publish=failing)),
        Obj(publish=failing),
        "identity",
    )
    incoming.ack.assert_not_awaited()
    incoming.nack.assert_awaited_once_with(requeue=True)


async def test_outbox_trace_is_carried_as_envelope_not_business_payload():
    db, session = database()
    row = {
        "id": uid(),
        "created_at": now(),
        "aggregate_id": uid(),
        "event_type": "test",
        "payload": {"__trace_id": "source-trace", "value": 1},
    }
    session.execute.side_effect = [MagicMock(mappings=lambda: Obj(all=lambda: [row])), None]
    exchange = Obj(publish=AsyncMock())
    await publish_pending(db, exchange, Obj(service_name="identity"))
    sent = json.loads(exchange.publish.call_args.args[0].body)
    assert sent["trace_id"] == "source-trace" and sent["payload"] == {"value": 1}
    assert "__trace_id" in row["payload"]
    assert session.execute.await_count == 2
