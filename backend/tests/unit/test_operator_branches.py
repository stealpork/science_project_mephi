import argparse
import builtins
import getpass
import json
import runpy
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from libs.platform import cli, config, migrate, queueops
from libs.platform.config import Settings

pytestmark = pytest.mark.unit


@asynccontextmanager
async def context(value):
    yield value


def database():
    session = Obj(
        scalar=AsyncMock(return_value=None),
        get=AsyncMock(return_value=None),
        add=MagicMock(),
        flush=AsyncMock(),
        begin=lambda: context(None),
    )
    return Obj(sessions=lambda: context(session), engine=Obj(dispose=AsyncMock())), session


@pytest.fixture
def operator(monkeypatch):
    db, session = database()
    settings = Settings()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "Database", lambda _: db)
    monkeypatch.setattr(cli, "append_event", AsyncMock())
    return settings, db, session


@pytest.mark.parametrize(
    "action,service",
    [
        ("create-admin", "people"),
        ("seed-users", "projects"),
        ("seed-projects", "identity"),
        ("seed-dictionaries", "identity"),
    ],
)
async def test_operator_wrong_service_cannot_write(operator, action, service):
    settings, _, session = operator
    settings.service_name = service
    with pytest.raises(SystemExit):
        await cli.run(Obj(action=action, count=1))
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "action,service", [("seed-users", "identity"), ("seed-projects", "projects")]
)
async def test_production_rejects_demo_data(operator, action, service):
    settings, _, session = operator
    settings.service_name, settings.environment = service, "production"
    with pytest.raises(SystemExit, match="non-production"):
        await cli.run(Obj(action=action, count=1))
    session.add.assert_not_called()


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("existing", [None, "user", "admin"])
async def test_create_admin_preserves_existing_credentials(
    operator, monkeypatch, capsys, interactive, existing
):
    _, db, session = operator
    if interactive:
        monkeypatch.delenv("ADMIN_EMAIL", raising=False)
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        monkeypatch.setattr(builtins, "input", lambda _: "ADMIN@example.org")
        monkeypatch.setattr(getpass, "getpass", lambda _: "Administrative2026!")
    else:
        monkeypatch.setenv("ADMIN_EMAIL", "ADMIN@example.org")
        monkeypatch.setenv("ADMIN_PASSWORD", "Administrative2026!")
    session.scalar.return_value = Obj(roles=[existing]) if existing else None
    if existing == "user":
        with pytest.raises(SystemExit, match="audited role"):
            await cli.run(Obj(action="create-admin"))
        session.add.assert_not_called()
    else:
        await cli.run(Obj(action="create-admin"))
        if existing:
            session.add.assert_not_called()
            assert "preserved" in capsys.readouterr().out
        else:
            row = session.add.call_args.args[0]
            assert row.email == "admin@example.org" and row.roles == ["user", "admin"]
            assert row.password_hash != "Administrative2026!" and row.email_verified
            db.engine.dispose.assert_awaited_once()
    assert "Administrative2026!" not in capsys.readouterr().out


async def test_short_admin_password_rejected_before_database_write(operator, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    with pytest.raises(SystemExit, match="12 characters"):
        await cli.run(Obj(action="create-admin"))
    operator[2].add.assert_not_called()


@pytest.mark.parametrize("secret", [None, "JBSWY3DPEHPK3PXP"])
async def test_admin_mfa_is_mandatory_and_encrypted(operator, monkeypatch, secret):
    settings, _, session = operator
    settings.admin_2fa_required = True
    key = Fernet.generate_key()
    settings.mfa_encryption_key = key.decode()
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("ADMIN_PASSWORD", "Administrative2026!")
    monkeypatch.delenv("ADMIN_TOTP_SECRET", raising=False)
    if not secret:
        with pytest.raises(SystemExit, match="ADMIN_TOTP_SECRET"):
            await cli.run(Obj(action="create-admin"))
        session.add.assert_not_called()
    else:
        monkeypatch.setenv("ADMIN_TOTP_SECRET", secret)
        await cli.run(Obj(action="create-admin"))
        encrypted = session.add.call_args.args[0].mfa_secret
        assert encrypted != secret and Fernet(key).decrypt(encrypted.encode()).decode() == secret


@pytest.mark.parametrize(
    "action,service",
    [("seed-users", "identity"), ("seed-projects", "projects"), ("seed-dictionaries", "people")],
)
async def test_seeds_create_only_missing_rows(operator, action, service):
    settings, db, session = operator
    settings.service_name = service
    session.get.side_effect = [Obj(id="existing"), None] if action != "seed-dictionaries" else None
    if action == "seed-dictionaries":
        session.get.side_effect = lambda *args: (
            Obj(id="exists") if len(session.get.await_args_list) % 2 else None
        )
    await cli.run(Obj(action=action, count=2))
    assert session.add.call_count >= 1
    if action == "seed-users":
        assert session.add.call_args.args[0].email == "student0001@demo.example.org"
        cli.append_event.assert_awaited_once()
    elif action == "seed-projects":
        assert session.add.call_count == 2
    else:
        assert {call.args[0].kind for call in session.add.call_args_list} >= {
            "skills",
            "team_roles",
        }
    db.engine.dispose.assert_awaited_once()


@pytest.fixture
def queues(monkeypatch):
    db, session = database()
    channel = Obj(declare_queue=AsyncMock(), default_exchange=Obj(publish=AsyncMock()))
    connection = Obj(channel=AsyncMock(return_value=channel))
    monkeypatch.setattr(
        queueops.aio_pika, "connect_robust", AsyncMock(side_effect=lambda _: context(connection))
    )
    monkeypatch.setattr(queueops, "get_settings", lambda: Settings())
    monkeypatch.setattr(queueops, "Database", lambda _: db)
    return db, session, channel


async def test_unknown_internal_action_does_not_create_or_promote_users(operator):
    _, db, session = operator
    await cli.run(Obj(action="unknown"))
    session.add.assert_not_called()
    cli.append_event.assert_not_awaited()
    db.engine.dispose.assert_awaited_once()


def arguments(**kwargs):
    return argparse.Namespace(
        **{
            "action": "replay",
            "service": "people",
            "reason": "operator fix",
            "operator": "operator",
            "limit": 2,
            **kwargs,
        }
    )


async def test_dlq_commands_require_identity(monkeypatch):
    monkeypatch.setattr(queueops, "get_settings", lambda: Settings(service_name="people"))
    with pytest.raises(SystemExit, match="identity"):
        await queueops.run(arguments())


@pytest.mark.parametrize("service", [None, "people"])
async def test_dlq_listing_is_read_only(queues, capsys, service):
    db, session, channel = queues
    channel.declare_queue.return_value = Obj(declaration_result=Obj(message_count=3))
    await queueops.run(arguments(action="list", service=service))
    results = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(results) == (1 if service else len(config.SERVICES))
    assert all(row["messages"] == 3 for row in results)
    session.add.assert_not_called()
    channel.default_exchange.publish.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [{"service": None}, {"reason": None}, {"operator": None}, {"limit": 0}, {"limit": 1001}],
)
async def test_dlq_replay_requires_explicit_audit_and_bounded_limit(queues, change):
    with pytest.raises(SystemExit, match="Replay requires"):
        await queueops.run(arguments(**change))
    queues[2].default_exchange.publish.assert_not_awaited()


@pytest.mark.parametrize("failure", [None, "schema", "publish", "audit"])
async def test_dlq_ack_only_after_confirmed_publish_and_durable_audit(queues, failure):
    db, session, channel = queues
    message = Obj(
        body=json.dumps(
            {"schema_version": 2 if failure == "schema" else 1, "event_id": "e"}
        ).encode(),
        message_id="e",
        ack=AsyncMock(),
        nack=AsyncMock(),
    )
    channel.declare_queue.return_value = Obj(get=AsyncMock(side_effect=[message, None]))
    if failure == "publish":
        channel.default_exchange.publish.side_effect = OSError("broker down")
    if failure == "audit":
        session.add.side_effect = RuntimeError("database down")
    if failure:
        with pytest.raises(
            SystemExit
            if failure == "schema"
            else (OSError if failure == "publish" else RuntimeError)
        ):
            await queueops.run(arguments())
        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)
    else:
        await queueops.run(arguments())
        message.ack.assert_awaited_once()
        audit = session.add.call_args.args[0]
        assert audit.action == "dlq_replay" and audit.detail["reason"] == "operator fix"
        published = channel.default_exchange.publish.call_args
        assert published.kwargs["mandatory"] and published.args[0].headers == {"attempts": 0}
    db.engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("action", ["upgrade", "current", "check", "revision"])
def test_migration_commands_have_service_paths_and_escaped_url(monkeypatch, action):
    monkeypatch.setattr(sys, "argv", ["migrate", action, "--message", "new schema"])
    monkeypatch.setattr(
        migrate, "get_settings", lambda: Settings(database_url="postgresql://user:p%25@db/identity")
    )
    called = MagicMock()
    monkeypatch.setattr(migrate.command, action, called)
    migrate.main()
    cfg = called.call_args.args[0]
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql://user:p%25@db/identity"
    assert cfg.get_main_option("version_locations").endswith(
        "services/identity/migrations/versions"
    )
    if action == "revision":
        assert called.call_args.kwargs == {"message": "new schema", "autogenerate": True}


@pytest.mark.parametrize(
    "module,argv",
    [
        ("libs.platform.migrate", ["migrate", "check"]),
        ("libs.platform.cli", ["cli", "seed-dictionaries"]),
        ("libs.platform.queueops", ["queueops", "list"]),
        ("libs.platform.serve", ["serve"]),
    ],
)
def test_operator_module_entrypoints(monkeypatch, module, argv):
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(config, "get_settings", lambda: Settings(service_name="people"))
    import uvicorn

    uvicorn_run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", uvicorn_run)
    monkeypatch.setattr(migrate.command, "check", MagicMock())
    ran = []

    def run(coroutine):
        ran.append(coroutine.cr_code.co_name)
        coroutine.close()

    monkeypatch.setattr(cli.asyncio, "run", run)
    monkeypatch.delitem(sys.modules, module, raising=False)
    runpy.run_module(module, run_name="__main__")
    if module.endswith("serve"):
        assert uvicorn_run.call_args.args == ("services.people.main:app",)
        assert uvicorn_run.call_args.kwargs["proxy_headers"] is False
    elif module.endswith("cli") or module.endswith("queueops"):
        assert ran == ["run"]


def test_importing_server_does_not_start_uvicorn(monkeypatch):
    import uvicorn

    run = MagicMock()
    monkeypatch.setattr(uvicorn, "run", run)
    runpy.run_module("libs.platform.serve", run_name="import_probe")
    run.assert_not_called()
