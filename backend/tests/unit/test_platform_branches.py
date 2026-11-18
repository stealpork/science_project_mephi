import asyncio
import base64
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from fastapi import FastAPI
from sqlalchemy import select

from libs.platform import api, deletion, http
from libs.platform.config import Settings
from libs.platform.database import Database, now, uid
from libs.platform.errors import APIError
from services.projects.models import Project
from tests.unit.support import database, request, rows

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "keys", [{"": "bad"}, {"x" * 101: "bad"}, {"key": "x" * 1025}, {"key": "not PEM"}, "rsa"]
)
def test_invalid_verification_keys_are_rejected(keys):
    if keys == "rsa":
        keys = {
            "old": generate_private_key(public_exponent=65537, key_size=2048)
            .public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        }
    with pytest.raises(ValueError, match="verification"):
        Settings(jwt_additional_public_keys=keys)


def test_service_url_cannot_target_arbitrary_host():
    with pytest.raises(ValueError, match="Unknown service"):
        Settings().service_url("http://attacker")


def test_non_identity_production_does_not_need_mfa_cipher():
    from tests.unit.test_production_config import production

    assert (
        production(service_name="people", mfa_encryption_key="", smtp_security="none").service_name
        == "people"
    )


def test_command_schema_handles_multiple_non_null_variants():
    class Input(api.Command):
        non_nullable_fields = frozenset({"value", "missing"})
        value: int | str | None = None

    schema = Input.model_json_schema()["properties"]["value"]
    assert {item["type"] for item in schema["anyOf"]} == {"string", "integer"}
    assert "default" not in schema
    assert Input.identifiers_are_uuids("unchanged") == "unchanged"

    class Identifiers(api.Command):
        ids: str

    assert Identifiers(ids="not-a-list").ids == "not-a-list"
    schema = {"properties": {"value": {"type": "string", "default": "retained"}}}
    api.command_schema(schema, Input)
    assert schema["properties"]["value"]["default"] == "retained"


@pytest.mark.parametrize("dns_failure", [False, True])
async def test_proxy_cache_refresh_resolves_only_trusted_hosts(monkeypatch, dns_failure):
    monkeypatch.setattr(api, "get_settings", lambda: Settings())
    app = api.create_app("identity")
    app.state.proxy_cache = (0, set())
    resolver = AsyncMock(
        side_effect=OSError("DNS unavailable") if dns_failure else None,
        return_value=[(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)

    @app.get("/api/ip")
    async def address(request: api.Request):
        return {"ip": request.state.client_ip}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/api/ip", headers={"X-Forwarded-For": "192.0.2.1"})
    assert response.json()["ip"] == ("127.0.0.1" if dns_failure else "192.0.2.1")
    assert resolver.await_count == 1


@pytest.mark.parametrize(
    "sort,value",
    [
        ("deadline", None),
        ("deadline", "date"),
        ("oldest", "date"),
        ("relevance", "date"),
        ("relevance-query", 3),
    ],
)
async def test_cursor_round_trip_handles_null_datetime_and_relevance(sort, value):
    query_sort = "relevance" if sort == "relevance-query" else sort
    query_string = "sort=" + query_sort + ("&q=Test" if sort == "relevance-query" else "")
    req = request(path="/api/projects", query=query_string)
    db = database()
    when = now()
    value = when if value == "date" else value
    project = Project(id=uid(), title="Test", created_at=when)
    db.execute.return_value = rows([(project, value), (project, value)])
    first = await api.paginate(db, select(Project), Project, req, 1)
    assert first["has_more"] and first["next_cursor"]
    req2 = request(path="/api/projects", query=query_string + "&cursor=" + first["next_cursor"])
    db.execute.return_value = rows([])
    second = await api.paginate(db, select(Project), Project, req2, 1)
    assert second == {"items": [], "has_more": False, "next_cursor": None}
    sql = str(db.execute.call_args.args[0])
    assert "IS NULL" in sql and "created_at" in sql


@pytest.mark.parametrize(
    "fault",
    ["too-long", "wrong-filter", "invalid-json", "missing-id", "invalid-date", "invalid-id"],
)
async def test_invalid_cursors_fail_closed_before_query(fault):
    req, db = request(path="/api/projects"), database()
    cipher = Fernet(
        base64.urlsafe_b64encode(
            hashlib.sha256((req.app.state.settings.service_token + ":cursor-v1").encode()).digest()
        )
    )
    fingerprint = hashlib.sha256(json.dumps([req.url.path, "viewer", []]).encode()).hexdigest()[:16]
    data = {
        "filter": "wrong" if fault == "wrong-filter" else fingerprint,
        "at": "bad" if fault == "invalid-date" else now().isoformat(),
        "id": "bad" if fault == "invalid-id" else uid(),
        "value": None,
    }
    if fault == "missing-id":
        data.pop("id")
    cursor = (
        "x" * 4097
        if fault == "too-long"
        else cipher.encrypt(
            b"not-json" if fault == "invalid-json" else json.dumps(data).encode()
        ).decode()
    )
    req.scope["query_string"] = ("cursor=" + cursor).encode()
    with pytest.raises(APIError) as error:
        await api.paginate(db, select(Project), Project, req)
    assert error.value.code == "INVALID_CURSOR"
    db.execute.assert_not_awaited()


async def test_expired_idempotency_record_is_replaced_not_replayed():
    req, db = request(headers={"Idempotency-Key": "new"}), database()
    metadata = Database(Settings()).metadata
    req.app.state.db = Obj(metadata=metadata)
    previous = {
        "created_at": now() - timedelta(hours=25),
        "request_hash": "old",
        "response": {"stale": True},
        "status": 201,
    }
    db.execute.side_effect = [None, Obj(mappings=lambda: Obj(first=lambda: previous)), None, None]
    op = AsyncMock(return_value={"fresh": True})
    response = await api.command(req, db, op, 202)
    assert response.status_code == 202 and json.loads(response.body) == {"fresh": True}
    op.assert_awaited_once()
    assert str(db.execute.call_args_list[2].args[0]).startswith("DELETE")


@pytest.mark.parametrize(
    "inserted,status", [(True, "deleting"), (False, "deleted"), (True, "active")]
)
async def test_deletion_handler_is_idempotent_and_requires_deleting_state(
    monkeypatch, inserted, status
):
    db, req = database(), request()
    req.app.state.db = Obj(metadata=Database(Settings()).metadata)
    db.scalar.return_value = "event" if inserted else None
    monkeypatch.setattr(deletion, "internal_call", AsyncMock(return_value={"status": status}))
    handler, app = AsyncMock(), FastAPI()
    deletion.install(app, handler)
    endpoint = next(route.endpoint for route in app.routes if route.name == "delete_user")
    if status == "active":
        with pytest.raises(APIError):
            await endpoint("u", deletion.DeleteInput(deletion_id=uid()), req, db)
        handler.assert_not_awaited()
    else:
        result = await endpoint("u", deletion.DeleteInput(deletion_id=uid()), req, db)
        assert result["status"] == "completed" and handler.await_count == int(inserted)


def test_moderator_requires_verified_second_factor_when_enabled(monkeypatch):
    monkeypatch.setattr(http, "get_settings", lambda: Settings(admin_2fa_required=True))
    with pytest.raises(APIError) as error:
        http.moderator({"roles": ["admin"], "mfa_verified": False})
    assert error.value.code == "MFA_REQUIRED"
    assert http.moderator({"roles": ["admin"], "mfa_verified": True})["mfa_verified"]


@pytest.mark.parametrize("key", [None, "x" * 101])
async def test_idempotency_key_is_required_and_bounded(key):
    db = database()
    with pytest.raises(APIError) as error:
        await api.command(request(headers={"Idempotency-Key": key} if key else {}), db, AsyncMock())
    assert error.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    db.execute.assert_not_awaited()


@pytest.mark.parametrize("state", ["new", "replay", "conflict"])
async def test_idempotency_replay_uses_validated_response_and_rejects_changed_body(state):
    from pydantic import BaseModel

    class Output(BaseModel):
        value: int

    req, db = request(headers={"Idempotency-Key": "same"}), database()
    req.scope["route"] = Obj(response_model=Output)
    req.app.state.db = Obj(metadata=Database(Settings()).metadata)
    previous = (
        None
        if state == "new"
        else {
            "created_at": now(),
            "request_hash": "different"
            if state == "conflict"
            else hashlib.sha256(b"{}").hexdigest(),
            "response": {"value": 7},
            "status": 201,
        }
    )
    db.execute.side_effect = [None, Obj(mappings=lambda: Obj(first=lambda: previous)), None]
    op = AsyncMock(return_value={"value": "7", "discarded": "private"})
    if state == "conflict":
        with pytest.raises(APIError) as error:
            await api.command(req, db, op)
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
        op.assert_not_awaited()
    else:
        result = await api.command(req, db, op, 201)
        assert json.loads(result.body) == {"value": 7} and result.status_code == 201
        assert op.await_count == int(state == "new")


def test_valid_public_key_configuration_iterates_all_keys():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    assert (
        len(
            Settings(
                jwt_additional_public_keys={"previous": public, "older": public}
            ).jwt_additional_public_keys
        )
        == 2
    )
