import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from dotenv import dotenv_values

pytestmark = pytest.mark.unit
path = Path(__file__).resolve().parents[3] / "deploy/scripts/ops.py"
spec = importlib.util.spec_from_file_location("science_ops_test", path)
assert spec and spec.loader
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)

init_spec = importlib.util.spec_from_file_location("science_init_test", path.with_name("init.py"))
assert init_spec and init_spec.loader
initializer = importlib.util.module_from_spec(init_spec)
init_spec.loader.exec_module(initializer)


@pytest.mark.parametrize("key_name", ["jwt_private.pem", "jwt_public.pem"])
def test_init_refuses_to_overwrite_orphan_signing_keys(monkeypatch, tmp_path, key_name):
    key = tmp_path / "deploy/.secrets/development" / key_name
    key.parent.mkdir(parents=True)
    key.write_text("original-key")
    monkeypatch.setattr(initializer, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["init.py"])
    with pytest.raises(SystemExit, match="instead of overwriting"):
        initializer.main()
    assert key.read_text() == "original-key"
    assert not (tmp_path / ".env").exists()


def test_init_preserves_existing_configuration(monkeypatch, tmp_path):
    config = tmp_path / ".env"
    config.write_text("existing-configuration")
    monkeypatch.setattr(initializer, "ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["init.py"])
    initializer.main()
    assert config.read_text() == "existing-configuration"
    assert not (tmp_path / "deploy/.secrets/development/jwt_private.pem").exists()


@pytest.mark.parametrize("script", ["deploy-prod.sh", "backup.sh", "restore.sh", "monitor.sh"])
def test_operator_wrappers_preserve_caller_relative_paths(tmp_path, script):
    executable = tmp_path / "uv"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
    executable.chmod(0o700)
    result = subprocess.run(
        [str(path.with_name(script)), "--env-file", "relative.env"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout.splitlines()[0] == str(tmp_path)
    assert "--project" in result.stdout
    assert result.stdout.splitlines()[-1] == "relative.env"


def test_ops_refuses_missing_configuration(tmp_path):
    with pytest.raises(SystemExit, match="refusing a fallback"):
        ops.Ops(tmp_path / "missing.env")


def test_common_resolves_env_and_overlay_before_changing_working_directory(tmp_path):
    settings, overlay = tmp_path / "config.env", tmp_path / "extra.yaml"
    settings.write_text("COMPOSE_PROJECT_NAME=science-test\n")
    overlay.write_text("services: {}\n")
    result = subprocess.run(
        [
            "sh",
            "-c",
            '. "$1"; printf "%s\\n" "$SC_ENV_FILE" "$SC_COMPOSE_EXTRA"',
            "guard-test",
            str(path.with_name("common.sh")),
        ],
        cwd=tmp_path,
        env={**os.environ, "SC_ENV_FILE": "config.env", "SC_COMPOSE_EXTRA": "extra.yaml"},
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.splitlines() == [str(settings), str(overlay)]


@pytest.mark.parametrize(
    "value", ["normal", "dollar-$value", "don't change", "back\\slash", "Кириллица", "line\nbreak"]
)
def test_restored_env_round_trips_special_characters(value):
    assert dotenv_values(stream=io.StringIO("VALUE=" + ops.quote_env(value)))["VALUE"] == value


def test_backup_rejects_external_storage_before_creating_snapshot(tmp_path):
    settings = SimpleNamespace(config={"S3_INTERNAL_ENDPOINT": "https://external.example.org"})
    with pytest.raises(SystemExit, match="external providers"):
        ops.backup(settings, tmp_path / "backup")
    assert not (tmp_path / "backup").exists()


def test_backup_cannot_recurse_into_source(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="recursive archives"):
        ops.backup(SimpleNamespace(config={}), tmp_path / "deploy/backup")


@pytest.mark.parametrize("resume_fails", [False, True])
def test_failed_dump_resumes_only_previous_writers_and_cleans_secrets(tmp_path, resume_fails):
    secret_dir = tmp_path / "keys"
    secret_dir.mkdir()
    (secret_dir / "key.pem").write_text("private-test-key")
    env = tmp_path / ".env"
    env.write_text("private-test-env")
    calls = []

    def compose(*arguments, **kwargs):
        calls.append(arguments)
        if arguments[0] == "exec":
            raise RuntimeError("synthetic dump failure")
        if arguments[0] == "start" and resume_fails:
            raise RuntimeError("synthetic resume failure")

    names = [
        *ops.APPS,
        "postgres",
        "minio",
        "rabbitmq",
        "redis",
        "mailpit",
        "prometheus",
        "grafana",
    ]
    fake = SimpleNamespace(
        config={"SECRET_DIRECTORY": str(secret_dir)},
        envfile=env,
        project="science-test",
        pin_images=MagicMock(),
        restic=MagicMock(),
        compose=compose,
        rows=lambda: [
            {"Service": name, "State": "exited" if name == "grafana" else "running"}
            for name in names
        ],
        container=lambda name: {"Config": {"Hostname": "rabbitmq"}},
    )
    destination = tmp_path / "backup"
    with pytest.raises(RuntimeError, match="synthetic"):
        ops.backup(fake, destination)
    resumed = next(arguments for arguments in calls if arguments[0] == "start")
    assert "--wait" in resumed and "grafana" not in resumed and "postgres" not in resumed
    assert set(ops.APPS) <= set(resumed)
    assert not list(destination.glob(".staging-*"))
    assert (
        json.loads((destination / "metrics/status.json").read_text())["last_attempt_success"] == 0
    )


def test_incomplete_stack_refusal_does_not_leave_decrypted_staging(tmp_path):
    fake = SimpleNamespace(config={}, pin_images=MagicMock(), restic=MagicMock(), rows=lambda: [])
    with pytest.raises(SystemExit, match="incomplete snapshot"):
        ops.backup(fake, tmp_path / "backup")
    assert not list((tmp_path / "backup").glob(".staging-*"))


@pytest.mark.parametrize(
    "target", ["science", "production", "science-test", "", "../science-restore-danger"]
)
def test_restore_never_accepts_a_working_project_name(tmp_path, target):
    with pytest.raises(SystemExit, match="science-restore"):
        ops.restore(None, tmp_path, target, "latest")


def test_restore_refuses_preexisting_project(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "call", lambda *args, **kwargs: b"existing-container")
    with pytest.raises(SystemExit, match="overwrite"):
        ops.restore(None, tmp_path, "science-restore-unit", "latest")


def test_restore_refuses_unlabelled_volume(monkeypatch, tmp_path):
    monkeypatch.setattr(ops, "call", lambda *args, **kwargs: b"")
    monkeypatch.setattr(
        ops.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    with pytest.raises(SystemExit, match="without Compose labels"):
        ops.restore(None, tmp_path, "science-restore-unit", "latest")


def test_ops_rejects_an_unrelated_compose_project(tmp_path):
    envfile = tmp_path / "runtime.env"
    envfile.write_text("COMPOSE_PROJECT_NAME=unrelated\n")
    with pytest.raises(SystemExit, match="unrelated"):
        ops.Ops(envfile)


def test_backup_telemetry_does_not_expose_credentials(tmp_path):
    ops.telemetry(tmp_path, success=1234, duration=20)
    ops.telemetry(tmp_path, success=1240, duration=6, restore=True)
    metrics = (tmp_path / "metrics/backup.prom").read_text()
    assert "science_backup_last_success_timestamp 1234" in metrics
    assert "science_backup_last_restore_check_timestamp 1240" in metrics
    ops.telemetry(tmp_path, failure=True)
    assert "science_backup_last_attempt_success 0" in (tmp_path / "metrics/backup.prom").read_text()


@pytest.mark.parametrize(
    "offsite_state,expected",
    [
        (None, 1),
        ({"success": False}, 1),
        ({"success": True, "verified_at": 1}, 1),
        ({"success": True, "rehearsal": True, "verified_at": 1}, 0),
    ],
)
def test_monitor_detects_failed_or_stale_configured_offsite_copy(
    tmp_path, capsys, offsite_state, expected
):
    ops.telemetry(tmp_path, success=ops.time.time(), duration=1)
    names = [*ops.APPS, "postgres", "redis", "rabbitmq", "minio", "mailpit", "clamav"]
    fake = SimpleNamespace(
        config={"OFFSITE_TARGET_ENV": "private.env"},
        rows=lambda: [{"Service": name, "State": "running", "Health": "healthy"} for name in names],
    )
    if offsite_state is not None:
        (tmp_path / "metrics/offsite.json").write_text(json.dumps(offsite_state))
    assert ops.status(fake, tmp_path, True) == expected
    report = json.loads(capsys.readouterr().out)
    assert report["healthy"] and report["offsite_attention_required"] == bool(expected)


@pytest.mark.parametrize(
    "age,success,exists,broken_service,expected",
    [
        (0, True, True, False, 0),
        (3601, True, True, False, 1),
        (0, False, True, False, 1),
        (0, False, False, False, 1),
        (0, True, True, True, 1),
    ],
)
def test_monitor_exit_code_includes_backup_health(
    tmp_path, capsys, age, success, exists, broken_service, expected
):
    names = [*ops.APPS, "postgres", "redis", "rabbitmq", "minio", "mailpit", "clamav"]
    fake = SimpleNamespace(
        config={},
        rows=lambda: [
            {
                "Service": name,
                "State": "exited" if broken_service and name == "identity" else "running",
                "Health": "healthy",
            }
            for name in names
        ],
    )
    if exists:
        ops.telemetry(tmp_path, success=ops.time.time() - age, duration=1)
        if not success:
            ops.telemetry(tmp_path, failure=True)
    assert ops.status(fake, tmp_path, True) == expected
    report = json.loads(capsys.readouterr().out)
    assert report["backup_attention_required"] == (not exists or not success or age > 3600)
    assert report["operationally_ready"] == (expected == 0)
