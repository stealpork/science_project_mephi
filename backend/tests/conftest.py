import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", help="Run against the dedicated Docker test stack"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        for item in items:
            if "e2e" in item.keywords or "integration" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="requires --live and isolated stack"))


def validate_live_target(environment=None, base_url=None):
    root = Path(__file__).resolve().parents[2]
    environment = Path(environment or os.environ.get("SC_ENV_FILE", root / ".env.test"))
    values = dotenv_values(environment)
    if (
        values.get("COMPOSE_PROJECT_NAME") != "science-test"
        or values.get("SC_ENVIRONMENT") != "test"
    ):
        raise pytest.UsageError(
            "Live tests require the isolated science-test environment, never production"
        )
    target = urlsplit(base_url or os.environ.get("TEST_BASE_URL", "http://localhost:18081"))
    if (
        target.scheme != "http"
        or target.hostname not in {"localhost", "127.0.0.1"}
        or target.port != int(values.get("APP_PORT", "18081"))
        or target.username
        or target.password
        or target.path not in {"", "/"}
        or target.query
        or target.fragment
    ):
        raise pytest.UsageError("Live tests may only write to the loopback port of science-test")
    return environment.resolve()


def verify_live_runtime(environment):
    try:
        identifiers = subprocess.check_output(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.project=science-test",
                "--filter",
                "label=com.docker.compose.service=nginx",
                "--format",
                "{{.ID}}",
            ],
            text=True,
            stderr=subprocess.PIPE,
            timeout=10,
        ).split()
        if len(identifiers) != 1:
            raise ValueError("Gateway not unique")
        ports = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .NetworkSettings.Ports}}",
                    identifiers[0],
                ],
                text=True,
                stderr=subprocess.PIPE,
                timeout=10,
            )
        )
        expected = str(dotenv_values(environment).get("APP_PORT", "18081"))
        if not any(
            item.get("HostIp") in {"127.0.0.1", "::1"} and item.get("HostPort") == expected
            for item in (ports.get("80/tcp") or [])
        ):
            raise ValueError("Wrong gateway binding")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        raise pytest.UsageError(
            "Live tests require exactly one science-test nginx with the declared loopback port"
        ) from None


def pytest_sessionstart(session):
    if session.config.getoption("--live"):
        verify_live_runtime(validate_live_target())


@pytest.fixture(scope="session", autouse=True)
def clean_live_stack(request):
    if not request.config.getoption("--live"):
        yield
        return
    environment = validate_live_target()

    def cleanup():
        subprocess.run(
            [sys.executable, "-m", "tests.cleanup", "--env-file", str(environment)],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            timeout=360,
        )

    cleanup()
    try:
        yield
    finally:
        cleanup()


@pytest_asyncio.fixture
async def api():
    async with httpx.AsyncClient(
        base_url=os.environ.get("TEST_BASE_URL", "http://localhost:18081"), timeout=30
    ) as client:
        yield client


def ok(response, status=200):
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:1200]}"
    )
    return response.json() if response.content else None


@pytest_asyncio.fixture
async def users(api, request):
    accounts = []

    for number in range(getattr(request, "param", 3)):
        email = f"test-{uuid4().hex}@example.org"
        password = "Science-tests-2026!"
        for _attempt in range(40):
            registration = await api.post(
                "/api/v1/auth/register",
                json={
                    "first_name": "Тест",
                    "last_name": f"Участник{number}",
                    "email": email,
                    "password": password,
                },
            )
            if registration.status_code != 429:
                break
            await asyncio.sleep(2)
        result = ok(registration, 201)
        pair = ok(await api.post("/api/v1/auth/login", json={"email": email, "password": password}))
        headers = {"Authorization": f"Bearer {pair['access_token']}"}
        for _ in range(100):
            response = await api.get("/api/v1/me/profile", headers=headers)
            if response.status_code == 200:
                break
            assert response.status_code == 202, response.text
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Registration outbox did not create a profile")
        accounts.append(
            {
                "id": result["user_id"],
                "email": email,
                "password": password,
                "headers": headers,
                **pair,
            }
        )
    return accounts


async def command(api, user, method, path, body=None, key=None):
    return await api.request(
        method, path, headers={**user["headers"], "Idempotency-Key": key or uuid4().hex}, json=body
    )


async def project(api, owner, access="public_open", size=3):
    area = ok(await api.get("/api/v1/dictionaries/research_areas"))["items"][0]["id"]
    row = ok(
        await command(
            api,
            owner,
            "POST",
            "/api/v1/projects",
            {
                "title": "Исследование для теста",
                "description": "Проверяем научное взаимодействие",
                "research_goal": "Провести исследование",
                "area_id": area,
                "access_type": access,
                "team_size": size,
            },
        ),
        201,
    )
    return ok(await command(api, owner, "POST", f"/api/v1/projects/{row['id']}/publish"))
