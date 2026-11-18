import asyncio

import pytest

from tests.conftest import ok
from tests.integration.test_infrastructure import compose

pytestmark = pytest.mark.integration


async def test_database_outage_is_retriable_not_an_unhandled_error(api):
    compose("stop", "-t", "10", "postgres")
    try:
        response = await api.get("/api/v1/projects")
        assert response.status_code == 503
        assert response.json()["error"]["code"] in {"DATABASE_UNAVAILABLE", "DATABASE_BUSY"}
    finally:
        compose("start", "--wait", "postgres")


async def test_identity_unavailable_fails_closed(api, users):
    user = users[0]
    compose("stop", "-t", "10", "identity")
    try:
        response = await api.get("/api/v1/me/profile", headers=user["headers"])
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    finally:
        compose("start", "--wait", "identity")


async def test_broker_outage_preserves_outbox_and_recovers(api, users):

    user = users[0]
    profile = ok(await api.get("/api/v1/me/profile", headers=user["headers"]))
    compose("stop", "-t", "10", "rabbitmq")
    try:
        updated = ok(
            await api.patch(
                "/api/v1/me/profile",
                headers=user["headers"],
                json={"version": profile["version"], "bio": "Записано во время отказа брокера"},
            )
        )
        assert updated["bio"] == "Записано во время отказа брокера"
        pending = compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-At",
            "-U",
            "people",
            "-d",
            "people",
            "-c",
            "SELECT count(*) FROM outbox_events WHERE published_at IS NULL",
        )
        assert int(pending.strip()) >= 1
    finally:
        compose("start", "--wait", "rabbitmq")
    for _ in range(100):
        pending = compose(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-At",
            "-U",
            "people",
            "-d",
            "people",
            "-c",
            "SELECT count(*) FROM outbox_events WHERE published_at IS NULL",
        )
        if pending.strip() == "0":
            break
        await asyncio.sleep(0.2)
    assert pending.strip() == "0"


async def test_missing_rate_limiter_does_not_disable_protection(api):
    compose("stop", "-t", "10", "redis")
    try:
        response = await api.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.org", "password": "not-a-valid-password"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "RATE_LIMIT_UNAVAILABLE"
    finally:
        compose("start", "--wait", "redis")
