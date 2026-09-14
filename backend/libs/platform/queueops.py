import argparse
import asyncio
import json

import aio_pika

from libs.platform.config import SERVICES, get_settings
from libs.platform.database import Database


async def run(args):
    settings = get_settings()
    if settings.service_name != "identity":
        raise SystemExit("Run operator commands in identity")
    async with await aio_pika.connect_robust(settings.rabbitmq_url) as connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        names = [args.service] if args.service else SERVICES
        if args.action == "list":
            for name in names:
                queue = await channel.declare_queue(f"science.{name}.dead", passive=True)
                print(
                    json.dumps(
                        {"service": name, "messages": queue.declaration_result.message_count}
                    )
                )
            return
        if not args.service or not args.reason or not args.operator or not 1 <= args.limit <= 1000:
            raise SystemExit("Replay requires --service, --reason, --operator and --limit 1..1000")
        queue = await channel.declare_queue(f"science.{args.service}.dead", passive=True)
        database = Database(settings)
        try:
            for _ in range(args.limit):
                message = await queue.get(fail=False)
                if message is None:
                    break
                event = json.loads(message.body)
                if event.get("schema_version") != 1:
                    await message.nack(requeue=True)
                    raise SystemExit(
                        "Unsupported schema remains in DLQ; deploy a compatible consumer first"
                    )
                try:
                    await channel.default_exchange.publish(
                        aio_pika.Message(
                            message.body,
                            content_type="application/json",
                            message_id=message.message_id,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                            headers={"attempts": 0},
                        ),
                        routing_key=f"science.{args.service}",
                        mandatory=True,
                    )
                    from services.identity.models import SecurityAudit

                    async with database.sessions() as session, session.begin():
                        session.add(
                            SecurityAudit(
                                actor_id=None,
                                action="dlq_replay",
                                target_id=event["event_id"],
                                detail={
                                    "service": args.service,
                                    "operator": args.operator,
                                    "reason": args.reason,
                                },
                            )
                        )
                    await message.ack()
                    print(
                        json.dumps(
                            {
                                "service": args.service,
                                "event_id": event["event_id"],
                                "status": "requeued",
                            }
                        )
                    )
                except BaseException:
                    await message.nack(requeue=True)
                    raise
        finally:
            await database.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "replay"])
    parser.add_argument("--service", choices=SERVICES)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--operator")
    parser.add_argument("--reason")
    asyncio.run(run(parser.parse_args()))
