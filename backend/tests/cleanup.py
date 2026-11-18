import argparse
import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values

from tests.conftest import validate_live_target, verify_live_runtime

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("identity", "people", "projects", "publications", "media", "activity")
APPS = (*SERVICES, *(f"{name}-worker" for name in SERVICES))
SQL = """
DO $$ DECLARE names text; BEGIN
  SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
  INTO names FROM pg_tables
  WHERE schemaname = 'public' AND tablename <> 'alembic_version';
  IF names IS NOT NULL THEN
    EXECUTE 'TRUNCATE TABLE ' || names || ' RESTART IDENTITY CASCADE';
  END IF;
END $$;
"""
INFRASTRUCTURE = """
import asyncio, base64, json
from urllib.parse import unquote, urlsplit, quote
from urllib.request import Request, urlopen
from redis.asyncio import Redis
from libs.platform.config import get_settings
from services.media.storage import s3_client

settings = get_settings()
assert settings.environment == 'test'
broker = urlsplit(settings.rabbitmq_url)
assert broker.hostname == 'rabbitmq'
assert urlsplit(settings.redis_url).hostname == 'redis'
assert urlsplit(settings.s3_internal_endpoint).hostname == 'minio'
credential = base64.b64encode(
    f'{unquote(broker.username)}:{unquote(broker.password)}'.encode()
).decode()
def management(path, method='GET'):
    request = Request('http://rabbitmq:15672/api/' + path, method=method,
                      headers={'Authorization': 'Basic ' + credential})
    with urlopen(request, timeout=30) as response:
        return response.read()
queues = json.loads(management('queues/%2F'))
for queue in sorted(queues, key=lambda item: not item['name'].endswith('.retry')):
    management('queues/%2F/' + quote(queue['name'], safe='') + '/contents', 'DELETE')
client = s3_client(settings)
for bucket in (settings.s3_quarantine_bucket, settings.s3_files_bucket):
    for page in client.get_paginator('list_object_versions').paginate(Bucket=bucket):
        objects = [{'Key': item['Key'], 'VersionId': item['VersionId']}
                   for item in page.get('Versions', []) + page.get('DeleteMarkers', [])]
        if objects:
            result = client.delete_objects(Bucket=bucket, Delete={'Objects': objects})
            assert not result.get('Errors'), 'Unable to remove test object versions'
async def clear_redis():
    client = Redis.from_url(settings.redis_url)
    try:
        await client.flushdb()
    finally:
        await client.aclose()
asyncio.run(clear_redis())
with urlopen(Request('http://mailpit:8025/api/v1/messages', method='DELETE'), timeout=30):
    pass
"""


def clean(environment=None):
    environment = validate_live_target(environment)
    verify_live_runtime(environment)
    config = dotenv_values(environment)
    overrides = (
        "RABBITMQ_URL",
        "REDIS_URL",
        "S3_INTERNAL_ENDPOINT",
        "S3_ENDPOINT_URL",
        *(f"{service.upper()}_DATABASE_URL" for service in SERVICES),
    )
    if any(config.get(key) for key in overrides):
        raise SystemExit("Test cleanup refuses external infrastructure overrides")
    prefix = [
        "docker",
        "compose",
        "--env-file",
        str(environment),
        "-p",
        "science-test",
        "-f",
        str(ROOT / "deploy/compose.yaml"),
        "-f",
        str(ROOT / "deploy/compose.local.yaml"),
    ]

    def compose(*args, source=None):
        result = subprocess.run(
            [*prefix, *args],
            input=source,
            capture_output=True,
            text=True,
            timeout=180,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in config and key not in overrides and not key.startswith("COMPOSE_")
            },
        )
        if result.returncode:
            raise RuntimeError(f"Test cleanup command failed: {' '.join(args[:4])}")
        return result.stdout

    running = set(compose("ps", "--services", "--status", "running").split())
    restart = [name for name in APPS if name in running]
    try:
        compose("stop", "-t", "30", *APPS)
        compose("run", "--rm", "--no-deps", "-T", "media", "python", "-", source=INFRASTRUCTURE)
        for service in SERVICES:
            compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "science",
                "-d",
                service,
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                SQL,
            )
        compose(
            "run",
            "--rm",
            "--no-deps",
            "people",
            "python",
            "-m",
            "libs.platform.cli",
            "seed-dictionaries",
        )
    finally:
        if restart:
            compose("start", "--wait", *restart)
    print("science-test cleaned: databases, S3 versions, queues, rate limits and captured mail")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    clean(parser.parse_args().env_file)
