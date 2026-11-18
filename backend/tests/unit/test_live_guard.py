import json

import pytest

from tests.conftest import validate_live_target, verify_live_runtime

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "target",
    [
        "https://production.example.org",
        "http://localhost:18080",
        "http://127.0.0.1:18081/api",
        "http://user:password@localhost:18081",
        "http://localhost:18081?production=true",
    ],
)
def test_live_tests_cannot_target_production_or_a_different_stack(tmp_path, target):
    settings = tmp_path / ".env.test"
    settings.write_text("COMPOSE_PROJECT_NAME=science-test\nSC_ENVIRONMENT=test\nAPP_PORT=18081\n")
    with pytest.raises(pytest.UsageError, match="loopback"):
        validate_live_target(settings, target)


@pytest.mark.parametrize(
    "project,mode",
    [("science", "development"), ("science", "production"), ("science-test", "production")],
)
def test_failure_injection_cannot_stop_working_services(tmp_path, project, mode):
    settings = tmp_path / ".env.test"
    settings.write_text(f"COMPOSE_PROJECT_NAME={project}\nSC_ENVIRONMENT={mode}\n")
    with pytest.raises(pytest.UsageError, match="isolated science-test"):
        validate_live_target(settings, "http://localhost:18081")


def test_dedicated_local_stack_is_allowed(tmp_path):
    settings = tmp_path / ".env.test"
    settings.write_text("COMPOSE_PROJECT_NAME=science-test\nSC_ENVIRONMENT=test\nAPP_PORT=18081\n")
    assert validate_live_target(settings, "http://127.0.0.1:18081") == settings.resolve()


@pytest.mark.parametrize(
    "identifiers,ports",
    [
        ("", {}),
        ("first\nsecond\n", {}),
        ("gateway\n", {}),
        ("gateway\n", {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "18081"}]}),
        ("gateway\n", {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]}),
        ("gateway\n", None),
    ],
)
def test_live_gateway_must_really_belong_to_the_test_project(
    monkeypatch, tmp_path, identifiers, ports
):
    settings = tmp_path / ".env.test"
    settings.write_text("APP_PORT=18081\n")
    outputs = iter([identifiers, json.dumps(ports)])
    monkeypatch.setattr(
        "tests.conftest.subprocess.check_output", lambda *args, **kwargs: next(outputs)
    )
    with pytest.raises(pytest.UsageError, match="science-test nginx"):
        verify_live_runtime(settings)


def test_actual_dedicated_gateway_binding_is_allowed(monkeypatch, tmp_path):
    settings = tmp_path / ".env.test"
    settings.write_text("APP_PORT=18081\n")
    outputs = iter(
        ["gateway\n", json.dumps({"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18081"}]})]
    )
    monkeypatch.setattr(
        "tests.conftest.subprocess.check_output", lambda *args, **kwargs: next(outputs)
    )
    verify_live_runtime(settings)


def test_unavailable_docker_does_not_allow_live_requests(monkeypatch, tmp_path):
    def unavailable(*args, **kwargs):
        raise OSError("unavailable")

    monkeypatch.setattr("tests.conftest.subprocess.check_output", unavailable)
    with pytest.raises(pytest.UsageError, match="science-test nginx"):
        verify_live_runtime(tmp_path / ".env.test")
