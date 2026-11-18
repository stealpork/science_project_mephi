import json

import pytest

from tests.integration.test_infrastructure import compose

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_operator_replay_preserves_id_and_writes_audit(api, users):
    result = compose(
        "exec",
        "-T",
        "people",
        "python",
        "-",
        source="""
import asyncio,json
import aio_pika
from sqlalchemy import select
from libs.platform.config import get_settings
from libs.platform.database import Database,uid
from services.people.models import Profile
async def main():
    settings=get_settings()
    assert settings.environment=="test"
    db=Database(settings)
    async with db.sessions() as session:
        user_id=await session.scalar(select(Profile.user_id).where(Profile.deleted.is_(False),Profile.blocked.is_(False)).limit(1))
    assert user_id
    async with await aio_pika.connect_robust(settings.rabbitmq_url) as connection:
        channel=await connection.channel(publisher_confirms=True)
        queue=await channel.declare_queue("science.people.dead",passive=True)
        if queue.declaration_result.message_count:
            print(json.dumps({"skip":"DLQ contains unrelated events; never replay them from a test"}))
            return
        identifier=uid()
        event={"schema_version":1,"event_id":identifier,"event_type":"identity.user_registered","payload":{"user_id":user_id,"first_name":"Ignored existing profile","last_name":"Ignored existing profile"}}
        exchange=await channel.get_exchange("science.dead")
        await exchange.publish(aio_pika.Message(json.dumps(event).encode(),message_id=identifier,delivery_mode=aio_pika.DeliveryMode.PERSISTENT),routing_key="people")
        print(json.dumps({"event_id":identifier}))
    await db.engine.dispose()
asyncio.run(main())
""",
    )
    data = json.loads(result.strip().splitlines()[-1])
    if data.get("skip"):
        pytest.skip(data["skip"])
    identifier = data["event_id"]
    output = compose(
        "exec",
        "-T",
        "identity",
        "python",
        "-m",
        "libs.platform.queueops",
        "replay",
        "--service",
        "people",
        "--limit",
        "1",
        "--operator",
        "integration-test",
        "--reason",
        "Replay only the synthetic registered-profile test event",
    )
    assert identifier in output
    compose(
        "exec",
        "-T",
        "identity",
        "python",
        "-",
        source="""
import asyncio
from sqlalchemy import select
from libs.platform.config import get_settings
from libs.platform.database import Database
from services.identity.models import SecurityAudit
async def main():
    db=Database(get_settings())
    async with db.sessions() as session:
        assert await session.scalar(select(SecurityAudit.id).where(SecurityAudit.target_id==EVENT_IDENTIFIER,SecurityAudit.action=="dlq_replay"))
    await db.engine.dispose()
asyncio.run(main())
""".replace("EVENT_IDENTIFIER", repr(identifier)),
    )


def test_retry_exhaustion_moves_only_the_test_event_to_dlq():
    compose(
        "exec",
        "-T",
        "identity",
        "python",
        "-",
        source="""
import asyncio,json
import aio_pika
from libs.platform.config import get_settings
from libs.platform.database import uid
async def main():
    settings=get_settings()
    assert settings.environment=="test"
    identifier=uid()
    async with await aio_pika.connect_robust(settings.rabbitmq_url) as connection:
        channel=await connection.channel(publisher_confirms=True)
        exchange=await channel.get_exchange("science.events")
        event={"schema_version":999,"event_id":identifier,"event_type":"identity.user_registered","payload":{}}
        await exchange.publish(aio_pika.Message(json.dumps(event).encode(),message_id=identifier,delivery_mode=aio_pika.DeliveryMode.PERSISTENT),routing_key=event["event_type"])
        queue=await channel.declare_queue("science.people.dead",passive=True)
        found=False
        for _ in range(260):
            message=await queue.get(fail=False)
            if message:
                if message.message_id==identifier:
                    assert int(message.headers["attempts"])==10
                    await message.ack()
                    found=True
                    break
                await message.nack(requeue=True)
            await asyncio.sleep(.2)
        assert found,"Retry policy did not route to DLQ within its bound"
asyncio.run(main())
""",
    )
