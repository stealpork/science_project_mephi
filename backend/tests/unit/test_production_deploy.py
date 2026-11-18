import importlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "deploy/scripts"))
production = importlib.import_module("production")
pytestmark = pytest.mark.unit
BACKEND = "ghcr.io/example/science/backend@sha256:" + "a" * 64
WEB = "ghcr.io/example/science/web@sha256:" + "b" * 64
STORAGE = "ghcr.io/example/science/storage@sha256:" + "c" * 64


def certificate(target, *, names=None, self_signed=False, expired=False, future=False):
    ca = ec.generate_private_key(ec.SECP256R1())
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "science.example.com")])
    issuer = (
        subject
        if self_signed
        else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fixture CA")])
    )
    now = datetime.now(UTC)
    value = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=1) if future else now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in names or ["science.example.com"]]
            ),
            critical=False,
        )
        .sign(ca, hashes.SHA256())
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / "server.crt").write_bytes(value.public_bytes(serialization.Encoding.PEM))
    (target / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (target / "server.key").chmod(0o600)


@pytest.fixture
def stack(monkeypatch, tmp_path):
    import hashlib

    secrets = tmp_path / "keys"
    secrets.mkdir()
    key = ed25519.Ed25519PrivateKey.generate()
    (secrets / "jwt_private.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (secrets / "jwt_private.pem").chmod(0o600)
    (secrets / "jwt_public.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    certificate(secrets / "tls/server")
    env = tmp_path / ".env.prod"
    env.write_text("SC_ENVIRONMENT=production\n")
    env.chmod(0o600)
    config = {
        "SC_ENVIRONMENT": "production",
        "SMTP_HOST": "smtp.example.com",
        "MAIL_FROM": "operator@example.com",
        "PUBLIC_URL": "https://science.example.com",
        "S3_PUBLIC_ENDPOINT": "https://science.example.com:9443",
        "ALLOWED_ORIGINS": '["https://science.example.com"]',
        "SCANNER_REQUIRED": "true",
        "ADMIN_2FA_REQUIRED": "true",
        "SMTP_SECURITY": "starttls",
        "MFA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "SECRET_DIRECTORY": str(secrets),
    }
    hashes = {}
    for name in production.SERVICES:
        token = name + "-" + "token" * 10
        config[f"{name.upper()}_SERVICE_TOKEN"] = token
        hashes[name] = hashlib.sha256(token.encode()).hexdigest()
    config["INTERNAL_TOKEN_HASHES"] = json.dumps(hashes)
    for name in [
        "POSTGRES_PASSWORD",
        "RABBITMQ_PASSWORD",
        "REDIS_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "S3_SECRET_ACCESS_KEY",
        "BACKUP_PASSPHRASE",
        *(f"{n.upper()}_DATABASE_PASSWORD" for n in production.SERVICES),
    ]:
        config[name] = name + "-" + "secret" * 8
    ops = Obj(
        config=config,
        envfile=env,
        project="science-prod",
        rows=MagicMock(return_value=[]),
        container=MagicMock(side_effect=lambda name: {"Image": "runtime-" + name}),
        compose=MagicMock(),
        pin_images=MagicMock(),
    )
    monkeypatch.setattr(production, "ROOT", tmp_path)
    return ops


@pytest.mark.parametrize(
    "digest", [BACKEND, "registry.example.com:5000/science/backend@sha256:" + "d" * 64]
)
def test_accepts_only_immutable_registry_images(digest):
    assert production.image(digest) == digest


@pytest.mark.parametrize(
    "digest",
    [
        None,
        "science:latest",
        "sha256:" + "a" * 64,
        BACKEND.upper(),
        BACKEND + "/",
        "https://" + BACKEND,
    ],
)
def test_rejects_ambiguous_or_mutable_images(digest):
    with pytest.raises(SystemExit, match="mutable tags"):
        production.image(digest)


def test_production_config_and_wildcard_certificate(stack):
    assert production.validate(stack) == Path(stack.config["SECRET_DIRECTORY"])
    certificate(Path(stack.config["SECRET_DIRECTORY"]) / "tls/server", names=["*.example.com"])
    assert production.validate(stack)


@pytest.mark.parametrize(
    "key,value,message",
    [
        ("SC_ENVIRONMENT", "development", "SC_ENVIRONMENT"),
        ("SMTP_HOST", "mailpit", "SMTP_HOST"),
        ("MAIL_FROM", "science@example.test", "MAIL_FROM"),
        ("PUBLIC_URL", "http://science.example.com", "HTTPS"),
        ("PUBLIC_URL", "https://user:secret@science.example.com", "HTTPS"),
        ("PUBLIC_URL", "https://science.example.com/path", "HTTPS"),
        ("PUBLIC_URL", "https://science.example.com?q=1", "HTTPS"),
        ("PUBLIC_URL", "https://science.example.com#x", "HTTPS"),
        ("S3_PUBLIC_ENDPOINT", "https://science.example.com", "port"),
        ("TLS_S3_PORT", "443", "port"),
        ("ALLOWED_ORIGINS", "[]", "ALLOWED_ORIGINS"),
        ("IDENTITY_SERVICE_TOKEN", "wrong", "hash mismatch"),
        ("BACKUP_PASSPHRASE", "weak", "strong secret"),
        ("S3_INTERNAL_ENDPOINT", "https://external.example.com", "local infrastructure"),
        ("REDIS_URL", "redis://external", "local infrastructure"),
    ],
)
def test_production_refuses_unsafe_configuration(stack, key, value, message):
    stack.config[key] = value
    with pytest.raises(SystemExit, match=message):
        production.validate(stack)


def test_rejects_two_listeners_on_one_port(stack):
    stack.config.update(
        TLS_APP_PORT="443", TLS_S3_PORT="443", S3_PUBLIC_ENDPOINT="https://science.example.com"
    )
    with pytest.raises(SystemExit, match="different host ports"):
        production.validate(stack)


@pytest.mark.parametrize("key", ["SCANNER_REQUIRED", "ADMIN_2FA_REQUIRED"])
def test_runtime_production_guards_cannot_be_bypassed_by_deployment(stack, key):
    stack.config[key] = "false"
    with pytest.raises(ValueError, match="requires scanner"):
        production.validate(stack)


@pytest.mark.parametrize("private", [False, True])
def test_secrets_must_not_be_world_readable(stack, private):
    target = (
        Path(stack.config["SECRET_DIRECTORY"]) / "jwt_private.pem" if private else stack.envfile
    )
    target.chmod(0o644)
    with pytest.raises(SystemExit, match="600"):
        production.validate(stack)


@pytest.mark.parametrize("key", ["jwt_private.pem", "jwt_public.pem"])
def test_requires_existing_signing_keys(stack, key):
    (Path(stack.config["SECRET_DIRECTORY"]) / key).unlink()
    with pytest.raises(SystemExit, match="Missing signing key"):
        production.validate(stack)


def test_rejects_mismatched_signing_keys(stack):
    key = ed25519.Ed25519PrivateKey.generate()
    (Path(stack.config["SECRET_DIRECTORY"]) / "jwt_public.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    with pytest.raises(SystemExit, match="do not match"):
        production.validate(stack)


@pytest.mark.parametrize(
    "variant", ["self-signed", "local-ca", "expired", "future", "wrong-host", "wrong-key"]
)
def test_checks_tls_identity_validity_and_key_pair(stack, variant):
    target = Path(stack.config["SECRET_DIRECTORY"]) / "tls/server"
    certificate(
        target,
        self_signed=variant == "self-signed",
        expired=variant == "expired",
        future=variant == "future",
        names=["other.example.com"] if variant == "wrong-host" else None,
    )
    if variant == "local-ca":
        (target.parent / "ca.key").write_text("fixture")
    if variant == "wrong-key":
        (target / "server.key").write_bytes(
            ec.generate_private_key(ec.SECP256R1()).private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    with pytest.raises(SystemExit):
        production.validate(stack)


def images():
    return {
        **dict.fromkeys(production.APPS, BACKEND),
        "nginx": WEB,
        "minio": STORAGE,
        **dict.fromkeys(production.INFRASTRUCTURE, BACKEND),
    }


def install_state(stack, monkeypatch):
    current = production.saved(stack)
    current.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "project": stack.project,
        "images": images(),
        "override": "current.compose.json",
        "container_images": {n: "runtime-" + n for n in production.APPS},
    }
    current.write_text(json.dumps(state))
    return state


@pytest.fixture
def deployment(stack, monkeypatch):
    calls = []

    def call(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return json.dumps([BACKEND]).encode()
        return b""

    monkeypatch.setattr(production, "call", call)
    monkeypatch.setattr(production, "configure", MagicMock())
    monkeypatch.setattr(production, "backup", MagicMock())
    monkeypatch.setattr(production, "Ops", lambda *args, **kwargs: stack)
    monkeypatch.setattr(production.httpx, "Client", MagicMock())
    args = Obj(
        action="install",
        backend_image=BACKEND,
        web_image=WEB,
        storage_image=STORAGE,
        release_file=None,
        schema_compatible=False,
    )
    return stack, args, calls


def test_initial_install_runs_all_migrations_and_publishes_success_manifest(deployment):
    ops, args, calls = deployment
    production.deploy(ops, args)
    state = json.loads(production.saved(ops).read_text())
    assert state["status"] == "succeeded" and state["images"] == images() and state["before"] == {}
    assert sum("upgrade" in c.args for c in ops.compose.call_args_list) == 6
    assert sum("check" in c.args for c in ops.compose.call_args_list) == 6
    assert any("minio-init" in c.args for c in ops.compose.call_args_list)
    assert not any("mailpit" in c.args for c in ops.compose.call_args_list)
    assert all(
        c[2].count("@sha256:") == 1 for c in calls if c[:2] == ["docker", "pull"] and "@" in c[2]
    )


def test_update_backs_up_before_migration_and_reuses_infrastructure_digests(
    deployment, monkeypatch
):
    ops, args, calls = deployment
    install_state(ops, monkeypatch)
    args.action = "apply"
    production.deploy(ops, args)
    production.backup.assert_called_once()
    assert not any("inspect" in c for c in calls)
    assert not any("minio-init" in c.args for c in ops.compose.call_args_list)


def test_rollback_requires_compatibility_and_never_downgrades_sql(
    deployment, monkeypatch, tmp_path
):
    ops, args, _ = deployment
    install_state(ops, monkeypatch)
    args.action = "rollback"
    with pytest.raises(SystemExit, match="schema-compatible"):
        production.deploy(ops, args)
    args.release_file = tmp_path / "release.json"
    args.schema_compatible = True
    args.release_file.write_text(json.dumps({"project": ops.project, "before": images()}))
    production.deploy(ops, args)
    assert not any("upgrade" in c.args or "downgrade" in c.args for c in ops.compose.call_args_list)


@pytest.mark.parametrize(
    "variant",
    [
        "already-installed",
        "missing-manifest",
        "wrong-project",
        "incomplete-rollback",
        "drift",
        "containers",
        "volumes",
        "failure",
    ],
)
def test_rollout_fails_closed_and_does_not_advance_current_manifest(
    deployment, monkeypatch, tmp_path, variant
):
    ops, args, calls = deployment
    if variant in {"already-installed", "wrong-project", "incomplete-rollback", "drift"}:
        state = install_state(ops, monkeypatch)
    if variant == "missing-manifest":
        args.action = "apply"
    if variant == "wrong-project":
        args.action = "apply"
        state["project"] = "science-other"
        production.saved(ops).write_text(json.dumps(state))
    if variant == "incomplete-rollback":
        args.action = "rollback"
        args.schema_compatible = True
        args.release_file = tmp_path / "rollback.json"
        args.release_file.write_text(json.dumps({"project": ops.project, "before": {"nginx": WEB}}))
    if variant == "drift":
        args.action = "apply"
        ops.container.side_effect = lambda name: {"Image": "unexpected"}
    if variant == "containers":
        ops.rows.return_value = [{"Service": "postgres"}]
    if variant == "volumes":
        monkeypatch.setattr(
            production,
            "call",
            lambda command, **kwargs: (
                json.dumps([BACKEND]).encode() if "inspect" in command else b"existing-volume"
            ),
        )
    if variant == "failure":
        ops.compose.side_effect = RuntimeError("failed compose")
    with pytest.raises((SystemExit, RuntimeError)):
        production.deploy(ops, args)
    if variant == "failure":
        manifest = next(
            p
            for p in production.saved(ops).parent.glob("*.json")
            if not p.name.endswith(".compose.json")
        )
        assert json.loads(manifest.read_text())["status"] == "failed_requires_operator_review"
        assert not production.saved(ops).exists()


@pytest.mark.parametrize(
    "action", ["validate", "plan", "status", "stop", "logs", "install", "apply", "rollback"]
)
def test_entrypoint_selects_explicit_action_without_shell_or_environment_leaks(
    stack, monkeypatch, action, capsys
):
    state = install_state(stack, monkeypatch)
    monkeypatch.setattr(production, "Ops", lambda *args, **kwargs: stack)
    validate = MagicMock()
    deploy = MagicMock()
    monkeypatch.setattr(production, "validate", validate)
    monkeypatch.setattr(production, "deploy", deploy)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "production.py",
            action,
            "--env-file",
            str(stack.envfile),
            "--backend-image",
            BACKEND,
            "--web-image",
            WEB,
            "--storage-image",
            STORAGE,
        ],
    )
    production.main()
    if action in {"install", "apply", "rollback"}:
        deploy.assert_called_once()
    elif action in {"status", "stop", "logs"}:
        stack.compose.assert_called_once()
    else:
        validate.assert_called_once()
    assert stack.config["BACKUP_PASSPHRASE"] not in capsys.readouterr().out
    assert state["project"] == stack.project


def test_production_wrapper_works_from_another_directory_without_import_path_setup(tmp_path):
    script = Path(__file__).resolve().parents[3] / "deploy/scripts/deploy-prod.sh"
    env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    result = subprocess.run(
        ["sh", str(script), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        "--backend-image" in result.stdout
        and "--web-image" in result.stdout
        and "--storage-image" in result.stdout
    )


@pytest.mark.parametrize("action", ["up", "stop", "status", "logs"])
def test_development_wrapper_cannot_target_production(tmp_path, action):
    script = Path(__file__).resolve().parents[3] / "deploy/scripts/deploy-dev.sh"
    config = tmp_path / "production.env"
    config.write_text("SC_ENVIRONMENT=production\n")
    result = subprocess.run(
        ["sh", str(script), action],
        env={**os.environ, "SC_ENV_FILE": str(config)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0 and "cannot target production" in result.stderr


def test_development_wrapper_ignores_ambient_project_override(tmp_path):
    script = Path(__file__).resolve().parents[3] / "deploy/scripts/deploy-dev.sh"
    config = tmp_path / "test.env"
    config.write_text("SC_ENVIRONMENT=test\nCOMPOSE_PROJECT_NAME=science-test\n")
    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "${COMPOSE_PROJECT_NAME-unset}" "$@"\n')
    docker.chmod(0o700)
    result = subprocess.run(
        ["sh", str(script), "status"],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SC_ENV_FILE": str(config),
            "COMPOSE_PROJECT_NAME": "unrelated",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines()[0] == "unset"
    assert str(config) in result.stdout and "compose.local.yaml" in result.stdout


def test_initial_production_configuration_has_separate_keys_and_no_working_mock_mail(
    monkeypatch, tmp_path
):
    initializer = importlib.import_module("init")
    monkeypatch.setattr(initializer, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["init.py", "--environment", "production"])
    initializer.main()
    from dotenv import dotenv_values

    values = dotenv_values(tmp_path / ".env.prod")
    assert (
        values["COMPOSE_PROJECT_NAME"] == "science-prod"
        and values["SC_ENVIRONMENT"] == "production"
    )
    assert values["ADMIN_2FA_REQUIRED"] == values["SCANNER_REQUIRED"] == "true"
    assert values["SMTP_HOST"] == "" and values["MAIL_FROM"] == ""
    assert (tmp_path / "deploy/.secrets/production/jwt_private.pem").exists()
    assert not (tmp_path / ".env").exists()
