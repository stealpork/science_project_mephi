import time
from contextlib import asynccontextmanager
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, TimeoutError

from libs.platform import api
from libs.platform.config import Settings
from libs.platform.errors import APIError

pytestmark = pytest.mark.unit


def application(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(service_name="identity"))
    app = api.create_app("identity")
    app.state.proxy_cache = (time.monotonic() + 60, {"10.0.0.2"})
    return app


@pytest.mark.parametrize(
    "error,status,code",
    [
        (APIError(409, "STATE", "State conflict"), 409, "STATE"),
        (HTTPException(404, "secret detail"), 404, "HTTP_ERROR"),
        (IntegrityError("private SQL", {}, Exception("secret database")), 409, "CONFLICT"),
        (DataError("private SQL", {}, Exception("secret database")), 422, "INVALID_IDENTIFIER"),
        (TimeoutError("secret pool"), 503, "DATABASE_BUSY"),
        (OSError("secret host"), 503, "DEPENDENCY_UNAVAILABLE"),
        (DBAPIError("private SQL", {}, Obj(sqlstate="57014")), 503, "DATABASE_BUSY"),
        (DBAPIError("private SQL", {}, Obj(sqlstate="08006")), 503, "DATABASE_BUSY"),
        (DBAPIError("private SQL", {}, Obj(sqlstate="XXXXX")), 500, "INTERNAL_ERROR"),
        (RuntimeError("secret exception"), 500, "INTERNAL_ERROR"),
    ],
)
async def test_errors_are_structured_retryable_and_do_not_leak(
    monkeypatch, caplog, error, status, code
):
    app = application(monkeypatch)

    @app.get("/api/test")
    async def failed():
        raise error

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get("/api/test", headers={"X-Request-ID": "known-trace"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"] == "known-trace"
    assert "secret" not in response.text and "private SQL" not in response.text
    assert "secret exception" not in caplog.text and "private SQL" not in caplog.text
    if status == 503:
        assert response.headers["Retry-After"] == "5"


@pytest.mark.parametrize(
    "peer,forwarded,expected",
    [
        ("10.0.0.2", "192.0.2.3", "192.0.2.3"),
        ("10.0.0.2", "not-an-ip", "10.0.0.2"),
        ("10.0.0.3", "192.0.2.3", "10.0.0.3"),
    ],
)
async def test_only_trusted_proxy_can_override_client_ip(monkeypatch, peer, forwarded, expected):
    app = application(monkeypatch)

    @app.get("/api/ip")
    async def address(request: Request):
        return {"ip": request.state.client_ip}

    transport = httpx.ASGITransport(app, client=(peer, 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/ip", headers={"X-Forwarded-For": forwarded, "X-Request-ID": "invalid request id!"}
        )
    assert response.json()["ip"] == expected
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Request-ID"] != "invalid request id!"


@pytest.mark.parametrize("failed", [False, True])
async def test_readiness_and_metrics_reflect_database_failure(monkeypatch, failed):
    app = application(monkeypatch)

    @asynccontextmanager
    async def connect():
        if failed:
            raise OSError("offline")
        yield Obj(execute=AsyncMock())

    app.state.db = Obj(engine=Obj(connect=connect))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/health/ready")).status_code == (503 if failed else 200)
        metrics = await client.get("/metrics")
    assert f'science_database_ready{{service="identity"}} {0 if failed else 1}.0' in metrics.text
