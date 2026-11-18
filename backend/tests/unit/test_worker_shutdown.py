import asyncio
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.platform import worker
from libs.platform.config import Settings
from tests.unit.support import context, database

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("stop_from", ["relay", "consumer"])
async def test_worker_graceful_shutdown_closes_resources_and_stops_consuming(
    monkeypatch, stop_from
):
    stopping, session = asyncio.Event(), database()
    db = Obj(sessions=lambda: context(session), engine=Obj(dispose=AsyncMock()))
    monkeypatch.setattr(worker, "get_settings", lambda: Settings())
    monkeypatch.setattr(worker, "Database", lambda _: db)
    handler = Obj(handle=AsyncMock())
    monkeypatch.setattr(worker.importlib, "import_module", lambda _: handler)
    monkeypatch.setattr(worker.asyncio, "Event", lambda: stopping)
    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", MagicMock())
    monkeypatch.setattr(worker, "start_http_server", MagicMock())
    client = Obj()
    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **kwargs: context(client))
    redis = Obj(aclose=AsyncMock())
    monkeypatch.setattr(worker.Redis, "from_url", lambda *args, **kwargs: redis)

    async def messages():
        if stop_from == "consumer":
            yield "message"
            pytest.fail("Consumer must stop after the in-flight message")

    queue = Obj(bind=AsyncMock(), iterator=lambda: context(messages()))
    channel = Obj(
        set_qos=AsyncMock(),
        declare_exchange=AsyncMock(),
        declare_queue=AsyncMock(return_value=queue),
    )
    connection = MagicMock(channel=AsyncMock(return_value=channel))
    connection.__aenter__.return_value = connection
    connection.__aexit__.return_value = False
    monkeypatch.setattr(worker.aio_pika, "connect_robust", AsyncMock(return_value=connection))
    published = []

    async def publish(*args):
        published.append(True)
        if stop_from == "relay":
            stopping.set()

    async def consume(*args):
        stopping.set()

    consumed = AsyncMock(side_effect=consume)
    monkeypatch.setattr(worker, "publish_pending", publish)
    monkeypatch.setattr(worker, "consume_one", consumed)

    async def to_thread(callback):

        assert callback.__self__.as_posix() == "/tmp/worker-ready"

    async def wait_for(awaitable, **options):
        assert options == {"timeout": 1}
        return await awaitable

    monkeypatch.setattr(worker.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(worker.asyncio, "wait_for", wait_for)
    await worker.main()
    assert published and consumed.await_count == int(stop_from == "consumer")
    redis.aclose.assert_awaited_once()
    db.engine.dispose.assert_awaited_once()
