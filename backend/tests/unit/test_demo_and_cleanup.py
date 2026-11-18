import importlib.util
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from tests import cleanup, conftest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("science_demo", ROOT / "deploy/scripts/demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
pytestmark = pytest.mark.unit


def environment(tmp_path, **values):
    file = tmp_path / ".env"
    config = {"SC_ENVIRONMENT": "development", "COMPOSE_PROJECT_NAME": "science", **values}
    file.write_text("\n".join(f"{key}={value}" for key, value in config.items()))
    return file


@pytest.mark.parametrize(
    "values",
    [
        {"SC_ENVIRONMENT": "production"},
        {"COMPOSE_PROJECT_NAME": "science-test"},
        {"IDENTITY_DATABASE_URL": "postgresql://remote/prod"},
        {"PUBLIC_URL": "https://science.example.org"},
        {"PUBLIC_URL": "http://localhost:18081"},
        {"PUBLIC_URL": "http://user:password@localhost:18080"},
        {"PUBLIC_URL": "http://localhost:18080/api"},
        {"PUBLIC_URL": "http://localhost:18080?remote=true"},
    ],
)
def test_demo_refuses_production_or_another_target(tmp_path, values):
    with pytest.raises(SystemExit):
        demo.validate_target(environment(tmp_path, **values))


def test_demo_uses_local_development_port(tmp_path):
    assert demo.validate_target(environment(tmp_path))[1] == "http://localhost:18080"


def test_demo_documents_are_real_two_page_pdfs():
    for project in demo.PROJECTS:
        data = demo.pdf_document(project["attachment"], project["sections"])
        document = PdfReader(io.BytesIO(data))
        assert len(document.pages) == 2
        assert document.metadata.title == project["attachment"]
        assert all(page.images for page in document.pages)
        assert len(data) < 25_000_000


def test_demo_has_distinct_profiles_and_access_modes():
    assert len({person["email"] for person in demo.PEOPLE}) == 2
    assert {project["access_type"] for project in demo.PROJECTS} == {
        "public_open",
        "public_invite",
        "private_invite",
    }
    for person in demo.PEOPLE:
        assert person["skills"] and person["interests"] and person["bio"]
        assert (demo.ASSETS / person["avatar"]).is_file()


@pytest.mark.parametrize("failure", [False, True])
def test_cleanup_only_wipes_test_databases_and_restarts_running_apps(
    monkeypatch, tmp_path, failure
):
    config = environment(tmp_path, SC_ENVIRONMENT="test", COMPOSE_PROJECT_NAME="science-test")
    calls = []
    monkeypatch.setattr(cleanup, "validate_live_target", lambda value: config)
    monkeypatch.setattr(cleanup, "verify_live_runtime", lambda value: None)

    def run(args, **kwargs):
        calls.append(args)
        assert args[args.index("-p") + 1] == "science-test"
        assert "compose.local.yaml" in " ".join(args)
        if "ps" in args:
            return SimpleNamespace(returncode=0, stdout="identity\npeople-worker\nnginx\n")
        return SimpleNamespace(returncode=int(failure and "TRUNCATE" in " ".join(args)), stdout="")

    monkeypatch.setattr(cleanup.subprocess, "run", run)
    if failure:
        with pytest.raises(RuntimeError, match="Test cleanup command failed"):
            cleanup.clean(config)
    else:
        cleanup.clean(config)
        wipes = [args for args in calls if "psql" in args]
        assert len(wipes) == 6
        assert {args[args.index("-d") + 1] for args in wipes} == set(cleanup.SERVICES)
        assert "tablename <> 'alembic_version'" in cleanup.SQL
        assert "seed-dictionaries" in " ".join(calls[-2])
    assert calls[-1][-4:] == ["start", "--wait", "identity", "people-worker"]


def test_cleanup_refuses_external_database_before_stopping_anything(monkeypatch, tmp_path):
    config = environment(tmp_path, IDENTITY_DATABASE_URL="postgresql://remote/prod")
    monkeypatch.setattr(cleanup, "validate_live_target", lambda value: config)
    monkeypatch.setattr(cleanup, "verify_live_runtime", lambda value: None)
    monkeypatch.setattr(
        cleanup.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must not stop services")
    )
    with pytest.raises(SystemExit, match="external infrastructure"):
        cleanup.clean(config)


def test_cleanup_failure_does_not_disclose_environment(monkeypatch, tmp_path):
    config = environment(tmp_path)
    monkeypatch.setattr(cleanup, "validate_live_target", lambda value: config)
    monkeypatch.setattr(cleanup, "verify_live_runtime", lambda value: None)
    monkeypatch.setattr(
        cleanup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stderr="private-password"),
    )
    with pytest.raises(RuntimeError, match="Test cleanup command failed") as error:
        cleanup.clean(config)
    assert "private-password" not in str(error.value)


@pytest.mark.parametrize("live", [False, True])
def test_live_fixture_cleans_before_and_after_a_suite(monkeypatch, tmp_path, live):
    config = tmp_path / ".env.test"
    calls = []
    monkeypatch.setattr(conftest, "validate_live_target", lambda: config)
    monkeypatch.setattr(conftest.subprocess, "run", lambda args, **kwargs: calls.append(args))
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: live))
    fixture = conftest.clean_live_stack.__wrapped__(request)
    next(fixture)
    assert len(calls) == int(live)
    with pytest.raises(StopIteration):
        next(fixture)
    assert len(calls) == 2 * int(live)
    if live:
        assert calls[0][-4:] == ["-m", "tests.cleanup", "--env-file", str(config)]


def test_live_fixture_still_cleans_on_interruption(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(conftest, "validate_live_target", lambda: tmp_path / ".env.test")
    monkeypatch.setattr(conftest.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: True))
    fixture = conftest.clean_live_stack.__wrapped__(request)
    next(fixture)
    with pytest.raises(KeyboardInterrupt):
        fixture.throw(KeyboardInterrupt())
    assert len(calls) == 2
