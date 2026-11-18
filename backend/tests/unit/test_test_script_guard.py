import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def script_harness(tmp_path):
    checkout = Path(__file__).resolve().parents[3]
    root = tmp_path / "isolated"
    scripts = root / "deploy/scripts"
    scripts.mkdir(parents=True)
    (root / "backend").mkdir()
    for name in ["test.sh", "init.py", "dev.sh", "common.sh"]:
        shutil.copy2(checkout / "deploy/scripts" / name, scripts / name)
    (root / ".env.test").write_text(
        "COMPOSE_PROJECT_NAME=science\nSC_ENVIRONMENT=development\nAPP_PORT=18080\n"
    )
    executables = tmp_path / "bin"
    executables.mkdir()
    marker = tmp_path / "unexpected-docker-call"
    (executables / "uv").write_text(
        '#!/bin/sh\nwhile [ "$1" != python ]; do shift; done\nshift\nexec "$SC_TEST_PYTHON" "$@"\n'
    )
    (executables / "docker").write_text(
        '#!/bin/sh\nprintf "project=%s\\n" "${COMPOSE_PROJECT_NAME-unset}" > "$SC_TEST_DOCKER_MARKER"\n'
        'printf "%s\\n" "$@" >> "$SC_TEST_DOCKER_MARKER"\nexit 99\n'
    )
    for executable in executables.iterdir():
        executable.chmod(0o700)
    environment = {
        **os.environ,
        "PATH": f"{executables}:{os.environ['PATH']}",
        "PYTHONPATH": str(checkout / "backend"),
        "SC_TEST_PYTHON": sys.executable,
        "SC_TEST_DOCKER_MARKER": str(marker),
        "TEST_BASE_URL": "http://localhost:18081",
    }
    return root, scripts / "test.sh", environment, marker


def invoke(harness, action):
    root, script, environment, _marker = harness
    return subprocess.run(
        [str(script), action],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("action", ["up", "fresh", "perf-up", "coverage", "tls"])
def test_mutating_test_commands_refuse_a_main_environment_before_docker(script_harness, action):
    result = invoke(script_harness, action)
    marker = script_harness[3]
    assert result.returncode != 0
    assert "isolated science-test" in result.stderr
    assert not marker.exists(), "Test command reached Docker before validating its target"


@pytest.mark.parametrize("action", ["up", "perf-up", "coverage"])
def test_ambient_compose_project_cannot_redirect_test_commands(script_harness, action):
    root, _script, environment, marker = script_harness
    (root / ".env.test").write_text(
        "COMPOSE_PROJECT_NAME=science-test\nSC_ENVIRONMENT=test\nAPP_PORT=18081\n"
    )
    environment["COMPOSE_PROJECT_NAME"] = "science"
    result = invoke(script_harness, action)
    assert result.returncode == 99
    arguments = marker.read_text().splitlines()
    if action == "up":
        assert arguments[0] == "project=science-test"
    else:
        assert arguments[arguments.index("-p") + 1] == "science-test"
