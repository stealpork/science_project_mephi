import asyncio
import importlib
import json
import logging
import signal
from pathlib import Path
from types import SimpleNamespace

import aio_pika
import httpx
from prometheus_client import Counter, Gauge, start_http_server
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from libs.platform.config import get_settings
from libs.platform.database import Database, now, uid

logger = logging.getLogger("science.worker")
SUBSCRIPTIONS = {
    "identity": ["identity.mail_requested", "identity.account_deletion_requested"],
    "people": ["identity.user_registered", "identity.user_blocked", "projects.project_updated"],
    "projects": [
        "publications.paper_changed",
        "media.file_rejected",
        "media.upload_cancelled",
        "media.upload_expired",
    ],
    "publications": ["activity.favorite_changed"],
    "media": ["media.upload_completed"],
    "activity": [
        "projects.#",
        "publications.#",
        "people.#",
        "media.file_rejected",
        "activity.action_requested",
    ],
}
logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
processed = Counter("science_worker_events_total", "Processed events", ["service", "result"])
outbox_age = Gauge("science_outbox_oldest_seconds", "Age of oldest unpublished event", ["service"])


async def publish_pending(db, exchange, settings):
    table = db.metadata.tables["outbox_events"]
    async with db.sessions() as session, session.begin():
        rows = (
            (
                await session.execute(
                    select(table)
                    .where(table.c.published_at.is_(None))
                    .order_by(table.c.created_at)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .all()
        )
        outbox_age.labels(settings.service_name).set(
            max(0, (now() - rows[0]["created_at"]).total_seconds()) if rows else 0
        )
        for row in rows:
            payload = dict(row["payload"])
            trace_id = payload.pop("__trace_id", row["id"])
            body = {
                "event_id": row["id"],
                "event_type": row["event_type"],
                "schema_version": 1,
                "occurred_at": row["created_at"].isoformat(),
                "producer": settings.service_name,
                "aggregate_id": row["aggregate_id"],
                "trace_id": trace_id,
                "payload": payload,
            }
            await exchange.publish(
                aio_pika.Message(
                    json.dumps(body).encode(),
                    content_type="application/json",
                    message_id=row["id"],
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=row["event_type"],
                mandatory=True,
            )
            await session.execute(
                update(table)
                .where(table.c.id == row["id"])
                .values(published_at=now(), attempts=table.c.attempts + 1)
            )


async def consume_one(message, db, context, handler, channel, dead, service):
    try:
        event = json.loads(message.body)
        if event.get("schema_version") != 1:
            raise ValueError("Unsupported event schema")
        table = db.metadata.tables["processed_events"]
        async with db.sessions() as session, session.begin():
            inserted = await session.scalar(
                insert(table)
                .values(event_id=event["event_id"], consumer=service)
                .on_conflict_do_nothing()
                .returning(table.c.event_id)
            )
            if inserted:
                context = SimpleNamespace(
                    app=context.app,
                    state=SimpleNamespace(request_id=event.get("trace_id", event["event_id"])),
                )
                session.info["trace_id"] = context.state.request_id
                await handler(context, session, event)
        await message.ack()
        processed.labels(service, "ok").inc()
    except Exception as exc:
        try:
            attempts = max(1, min(10, int((message.headers or {}).get("attempts", 0)) + 1))
        except (ValueError, TypeError):
            attempts = 10
        logger.error(
            json.dumps(
                {
                    "service": service,
                    "event_id": message.message_id,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                    "attempt": attempts,
                }
            )
        )
        retry = aio_pika.Message(
            message.body,
            content_type="application/json",
            message_id=message.message_id,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers={"attempts": attempts},
        )
        try:
            if attempts >= 10:
                await dead.publish(retry, routing_key=service, mandatory=True)
                processed.labels(service, "dead").inc()
            else:
                await channel.default_exchange.publish(
                    retry, routing_key=f"science.{service}.retry", mandatory=True
                )
                processed.labels(service, "retry").inc()
            await message.ack()
        except Exception:
            await message.nack(requeue=True)


async def main():
    settings = get_settings()
    service = settings.service_name
    db = Database(settings)
    handler_module = importlib.import_module(f"services.{service}.events")
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    start_http_server(8001)
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as http:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        context = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(settings=settings, db=db, http=http, redis=redis)
            ),
            state=SimpleNamespace(request_id=uid()),
        )
        connection = await aio_pika.connect_robust(
            settings.rabbitmq_url, fail_fast=False, reconnect_interval=2
        )
        async with connection:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            await channel.set_qos(prefetch_count=10)
            exchange = await channel.declare_exchange(
                "science.events", aio_pika.ExchangeType.TOPIC, durable=True
            )
            dead = await channel.declare_exchange(
                "science.dead", aio_pika.ExchangeType.DIRECT, durable=True
            )
            queue = None
            for consumer_name, patterns in SUBSCRIPTIONS.items():
                target = await channel.declare_queue(f"science.{consumer_name}", durable=True)
                dead_queue = await channel.declare_queue(
                    f"science.{consumer_name}.dead", durable=True
                )
                await dead_queue.bind(dead, routing_key=consumer_name)
                await channel.declare_queue(
                    f"science.{consumer_name}.retry",
                    durable=True,
                    arguments={
                        "x-message-ttl": 5000,
                        "x-dead-letter-exchange": "science.events",
                        "x-dead-letter-routing-key": f"retry.{consumer_name}",
                    },
                )
                await target.bind(exchange, routing_key=f"retry.{consumer_name}")
                for pattern in patterns:
                    await target.bind(exchange, routing_key=pattern)
                if consumer_name == service:
                    queue = target
            assert queue is not None

            audit_queue = await channel.declare_queue(
                "science.audit",
                durable=True,
                arguments={"x-max-length": 10000, "x-overflow": "drop-head"},
            )
            await audit_queue.bind(exchange, routing_key="#")

            async def relay():
                tick = 0
                while not stopping.is_set():
                    try:
                        await publish_pending(db, exchange, settings)
                        await asyncio.to_thread(Path("/tmp/worker-ready").touch)
                        tick += 1
                        if tick % 60 == 0 and hasattr(handler_module, "reconcile"):
                            async with db.sessions() as session, session.begin():
                                await handler_module.reconcile(context, session)
                    except Exception as exc:
                        logger.error(
                            json.dumps(
                                {
                                    "service": service,
                                    "task": "relay",
                                    "error_type": type(exc).__name__,
                                }
                            )
                        )
                    try:
                        await asyncio.wait_for(stopping.wait(), timeout=1)
                    except TimeoutError:
                        pass

            async def consumer():
                async with queue.iterator() as iterator:
                    async for message in iterator:
                        await consume_one(
                            message, db, context, handler_module.handle, channel, dead, service
                        )
                        if stopping.is_set():
                            break

            tasks = [asyncio.create_task(relay()), asyncio.create_task(consumer())]
            await stopping.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await redis.aclose()
    await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
