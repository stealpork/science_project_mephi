import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.conftest import validate_live_target

ROOT = Path(__file__).resolve().parents[3]
SERVICES = ("identity", "people", "projects", "publications", "media", "activity")
pytestmark = pytest.mark.integration


def compose(*args, source=None):
    environment = validate_live_target()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(environment),
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            *args,
        ],
        input=source,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


@pytest.mark.parametrize("service", SERVICES)
def test_schema_matches_migrations(service):
    compose("exec", "-T", service, "python", "-m", "libs.platform.migrate", "check")


@pytest.mark.parametrize("service", SERVICES)
def test_database_roles_cannot_connect_to_other_service(service):
    other = "people" if service != "people" else "identity"
    value = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-At",
        "-U",
        service,
        "-d",
        service,
        "-c",
        f"SELECT has_database_privilege(current_user, '{other}', 'CONNECT')",
    )
    assert value.strip() == "f"


def test_outbox_is_atomic_with_domain_transaction():
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
from libs.platform.database import Database, append_event, uid
async def main():
    settings = get_settings()
    assert settings.environment == "test"
    db = Database(settings)
    async with db.sessions() as session:
        identifier = uid()
        await append_event(session, "test.rollback", identifier, {})
        await session.rollback()
    async with db.sessions() as session:
        table = db.metadata.tables["outbox_events"]
        assert await session.scalar(select(table.c.id).where(table.c.aggregate_id == identifier)) is None
    await db.engine.dispose()
asyncio.run(main())
""",
    )


def test_duplicate_broker_delivery_has_one_business_effect():
    identifier = str(uuid4())
    compose(
        "exec",
        "-T",
        "activity",
        "python",
        "-",
        source="""
import asyncio
from libs.platform.config import get_settings
from libs.platform.database import Database,uid
from services.activity.models import Favorite
async def main():
    db=Database(get_settings())
    async with db.sessions() as session,session.begin():
        session.add(Favorite(user_id=uid(),target_type="paper",target_id=PAPER_IDENTIFIER))
    await db.engine.dispose()
asyncio.run(main())
""".replace("PAPER_IDENTIFIER", repr(identifier)),
    )
    compose(
        "exec",
        "-T",
        "publications",
        "python",
        "-",
        source="""
import asyncio, json
import aio_pika
from sqlalchemy import select
from libs.platform.config import get_settings
from libs.platform.database import Database, uid
from services.publications.models import Paper
async def main():
    settings = get_settings()
    assert settings.environment == "test"
    db = Database(settings)
    identifier = PAPER_IDENTIFIER
    async with db.sessions() as session, session.begin():
        session.add(Paper(id=identifier, title="Duplicate delivery integration test", creator_id=uid()))
    event = {"schema_version":1,"event_id":uid(),"event_type":"activity.favorite_changed","payload":{"type":"paper","target_id":identifier,"delta":-1}}
    async with await aio_pika.connect_robust(settings.rabbitmq_url) as connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.get_exchange("science.events")
        for _ in range(2):
            await exchange.publish(aio_pika.Message(json.dumps(event).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT),routing_key=event["event_type"])
    for _ in range(100):
        async with db.sessions() as session:
            count = await session.scalar(select(Paper.saves_count).where(Paper.id==identifier))
        if count:
            break
        await asyncio.sleep(.1)
    await asyncio.sleep(.5)
    async with db.sessions() as session:
        assert await session.scalar(select(Paper.saves_count).where(Paper.id==identifier)) == 1
    await db.engine.dispose()
asyncio.run(main())
""".replace("PAPER_IDENTIFIER", repr(identifier)),
    )


def test_antivirus_rejects_eicar():
    compose(
        "exec",
        "-T",
        "media",
        "python",
        "-",
        source="""
import asyncio
from libs.platform.config import get_settings
from services.media.events import antivirus
async def main():
    payload = b"X5O!P%@AP[4\\\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    try:
        await antivirus(get_settings(), payload)
    except ValueError as exc:
        assert str(exc) == "MALWARE_FOUND"
    else:
        raise AssertionError("Scanner did not reject EICAR")
asyncio.run(main())
""",
    )


def test_internal_api_requires_service_token():
    compose(
        "exec",
        "-T",
        "activity",
        "python",
        "-",
        source="""
import httpx
assert httpx.get("http://identity:8000/internal/v1/users").status_code == 403
assert httpx.get("http://people:8000/internal/v1/profiles/00000000-0000-0000-0000-000000000000", headers={"X-Service-Name":"activity","X-Service-Token":"wrong"}).status_code == 403
""",
    )


async def test_s3_buckets_are_not_public():
    base = os.environ.get("TEST_S3_URL", "http://localhost:19001")
    async with httpx.AsyncClient() as client:
        for bucket in ["science-quarantine", "science-files"]:
            assert (await client.get(f"{base}/{bucket}")).status_code == 403


def test_worker_and_api_health():
    rows = [
        json.loads(line) for line in compose("ps", "--format", "json").splitlines() if line.strip()
    ]
    statuses = {row["Service"]: row for row in rows}
    for service in [*SERVICES, *(f"{name}-worker" for name in SERVICES)]:
        assert statuses[service]["Health"] == "healthy"
