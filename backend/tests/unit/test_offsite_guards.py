import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def offsite(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "deploy/scripts"))
    return importlib.import_module("offsite")


def credentials(tmp_path, **changes):
    values = {
        "OFFSITE_REPOSITORY": "s3:https://backup.example.org/science/repository",
        "OFFSITE_PASSWORD": "repository-password-for-tests-only-2026",
        "OFFSITE_AWS_ACCESS_KEY_ID": "backup-writer",
        "OFFSITE_AWS_SECRET_ACCESS_KEY": "private-key",
    }
    values.update(changes)
    target = tmp_path / ".env.offsite"
    target.write_text("\n".join(f"{key}='{value}'" for key, value in values.items()))
    target.chmod(0o600)
    return target


@pytest.mark.parametrize(
    "changes",
    [
        {"OFFSITE_REPOSITORY": "s3:http://backup.example.org/bucket"},
        {"OFFSITE_REPOSITORY": "s3:https://user:password@backup.example.org/bucket"},
        {"OFFSITE_REPOSITORY": "s3:https://backup.example.org/bucket?token=private"},
        {"OFFSITE_REPOSITORY": "sftp:server:/repository"},
        {"OFFSITE_PASSWORD": "short"},
        {"OFFSITE_AWS_ACCESS_KEY_ID": "science-app"},
        {"OFFSITE_REPOSITORY": "s3:http://localhost:19000/bucket", "OFFSITE_REHEARSAL": "true"},
    ],
)
def test_offsite_rejects_unsafe_or_recursive_configuration(offsite, tmp_path, changes):
    with pytest.raises(SystemExit):
        offsite.remote_settings(SimpleNamespace(config={}), credentials(tmp_path, **changes))


def test_offsite_secret_file_is_never_world_readable(offsite, tmp_path):
    target = credentials(tmp_path)
    target.chmod(0o644)
    with pytest.raises(SystemExit, match="private file"):
        offsite.remote_settings(SimpleNamespace(config={}), target)


def test_separate_local_s3_is_explicitly_only_a_rehearsal(offsite, tmp_path):
    target = credentials(
        tmp_path, OFFSITE_REPOSITORY="s3:http://localhost:19001/drill", OFFSITE_REHEARSAL="true"
    )
    _, rehearsal = offsite.remote_settings(SimpleNamespace(config={}), target)
    assert rehearsal
    with pytest.raises(SystemExit, match="Production"):
        offsite.remote_settings(SimpleNamespace(config={"SC_ENVIRONMENT": "production"}), target)


def test_offsite_process_arguments_do_not_contain_credentials(offsite, monkeypatch):
    observed = {}

    def run(arguments, **kwargs):
        observed.update(arguments=arguments, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(offsite.subprocess, "run", run)
    values = {
        "OFFSITE_REPOSITORY": "s3:https://backup.example.org/bucket",
        "OFFSITE_PASSWORD": "private-password",
        "OFFSITE_AWS_ACCESS_KEY_ID": "backup-writer",
        "OFFSITE_AWS_SECRET_ACCESS_KEY": "private-key",
    }
    offsite.execute(
        SimpleNamespace(config={"BACKUP_PASSPHRASE": "private-local"}), values, ["check"]
    )
    arguments = json.dumps(observed["arguments"])
    assert not any(
        secret in arguments for secret in ["private-password", "private-key", "private-local"]
    )
    assert observed["env"]["RESTIC_PASSWORD"] == "private-password"


def test_remote_recovery_does_not_require_the_lost_local_repository(offsite, monkeypatch, tmp_path):
    state = tmp_path / "empty-operator-state"
    destination = tmp_path / "recovered/repository"
    monkeypatch.setattr(
        offsite,
        "Ops",
        lambda *args: SimpleNamespace(config={"BACKUP_PASSPHRASE": "new-local-password"}),
    )
    monkeypatch.setattr(
        offsite,
        "remote_settings",
        lambda *args: ({"OFFSITE_REPOSITORY": "s3:https://backup.example.org/bucket"}, True),
    )
    execute = MagicMock()
    monkeypatch.setattr(offsite, "execute", execute)
    monkeypatch.setattr(
        "sys.argv",
        [
            "offsite.py",
            "fetch",
            "--target-env",
            "private.env",
            "--directory",
            str(state),
            "--destination",
            str(destination),
        ],
    )
    offsite.main()
    assert destination.is_dir() and not (state / "repository/config").exists()
    assert execute.call_count == 3
    assert all(
        call.kwargs["fetch"] is True and "source" not in call.kwargs
        for call in execute.call_args_list
    )


def test_failed_offsite_copy_does_not_erase_previous_verified_time(offsite, tmp_path):
    offsite.copy_telemetry(tmp_path, success=True, rehearsal=False)
    target = tmp_path / "metrics/offsite.json"
    verified = json.loads(target.read_text())["verified_at"]
    offsite.copy_telemetry(tmp_path, success=False, rehearsal=False)
    state = json.loads(target.read_text())
    assert state["success"] is False and state["verified_at"] == verified
    metrics = (tmp_path / "metrics/offsite.prom").read_text()
    assert "science_offsite_last_attempt_success 0\n" in metrics
    assert "science_offsite_rehearsal 0\n" in metrics
    assert not list((tmp_path / "metrics").glob("*.tmp"))


def test_first_failed_rehearsal_is_not_reported_as_verified(offsite, tmp_path):
    offsite.copy_telemetry(tmp_path, success=False, rehearsal=True)
    state = json.loads((tmp_path / "metrics/offsite.json").read_text())
    assert "verified_at" not in state and state["rehearsal"] is True
    metrics = (tmp_path / "metrics/offsite.prom").read_text()
    assert "science_offsite_last_success_timestamp 0\n" in metrics
    assert "science_offsite_rehearsal 1\n" in metrics
