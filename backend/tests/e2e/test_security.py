import asyncio
import base64
import hashlib
import os
import secrets
import subprocess
from uuid import uuid4

import pyotp
import pytest

from tests.conftest import command, ok, project
from tests.integration.test_infrastructure import ROOT

pytestmark = pytest.mark.e2e


@pytest.fixture
def admin_credentials():
    email = f"admin-{uuid4().hex}@example.org"
    password = secrets.token_urlsafe(24)
    env = os.environ.copy()
    env.update(ADMIN_EMAIL=email, ADMIN_PASSWORD=password)
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(ROOT / ".env.test"),
            "-f",
            str(ROOT / "deploy/compose.yaml"),
            "exec",
            "-T",
            "-e",
            "ADMIN_EMAIL",
            "-e",
            "ADMIN_PASSWORD",
            "identity",
            "python",
            "-m",
            "libs.platform.cli",
            "create-admin",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return email, password


async def test_moderation_blocking_and_dictionary_decision(api, users, admin_credentials):
    owner, outsider, _ = users
    email, password = admin_credentials
    pair = ok(await api.post("/api/v1/auth/login", json={"email": email, "password": password}))
    admin = {"headers": {"Authorization": "Bearer " + pair["access_token"]}}
    row = await project(api, owner)
    assert (await api.get("/api/v1/admin/users", headers=outsider["headers"])).status_code == 403
    result = ok(
        await command(
            api,
            admin,
            "POST",
            f"/api/v1/admin/projects/{row['id']}/hide",
            {"reason": "Модерация тестовой публикации"},
        ),
        202,
    )
    assert result["status"] == "succeeded"
    assert (await api.get(f"/api/v1/projects/{row['id']}")).status_code == 404
    ok(
        await command(
            api,
            admin,
            "POST",
            f"/api/v1/admin/projects/{row['id']}/restore",
            {"reason": "Проверка завершена"},
        ),
        202,
    )
    assert (await api.get(f"/api/v1/projects/{row['id']}")).status_code == 200
    suggestion = ok(
        await command(
            api,
            outsider,
            "POST",
            "/api/v1/dictionaries/suggestions",
            {"kind": "skills", "name": "Навык " + uuid4().hex},
        ),
        201,
    )
    decision = ok(
        await command(
            api,
            admin,
            "POST",
            f"/api/v1/admin/dictionary-suggestions/{suggestion['id']}/approve",
            {},
        )
    )
    assert decision["status"] == "approved"
    ok(
        await command(
            api,
            admin,
            "POST",
            f"/api/v1/admin/users/{outsider['id']}/block",
            {"reason": "Тест блокировки"},
        ),
        202,
    )
    assert (await api.get("/api/v1/me/profile", headers=outsider["headers"])).status_code in {
        401,
        403,
    }


async def test_mfa_requires_valid_totp_and_supports_recovery(api, users):
    owner = users[0]
    setup = ok(
        await api.post(
            "/api/v1/account/2fa/setup",
            headers=owner["headers"],
            json={"current_password": owner["password"]},
        )
    )
    otp = pyotp.parse_uri(setup["provisioning_uri"])
    enabled = ok(
        await api.post(
            "/api/v1/account/2fa/enable",
            headers=owner["headers"],
            json={"code": otp.now()},
        )
    )
    challenge = ok(
        await api.post(
            "/api/v1/auth/login", json={"email": owner["email"], "password": owner["password"]}
        )
    )
    assert challenge["requires_2fa"] is True
    assert (
        await api.post(
            "/api/v1/auth/2fa/challenge",
            json={"challenge_token": challenge["challenge_token"], "code": "000000"},
        )
    ).status_code == 403
    pair = ok(
        await api.post(
            "/api/v1/auth/2fa/challenge",
            json={
                "challenge_token": challenge["challenge_token"],
                "recovery_code": enabled["recovery_codes"][0],
            },
        )
    )
    assert pair["access_token"]
    assert (
        await api.post(
            "/api/v1/auth/2fa/challenge",
            json={
                "challenge_token": challenge["challenge_token"],
                "recovery_code": enabled["recovery_codes"][0],
            },
        )
    ).status_code == 400


async def test_file_reservations_serialize_and_limits_enforced(api, users):
    owner = users[0]
    row = await project(api, owner)
    payload = {
        "purpose": "project_pdf",
        "resource_id": row["id"],
        "filename": "test.pdf",
        "content_type": "application/pdf",
        "size_bytes": 100,
        "checksum_sha256": base64.b64encode(hashlib.sha256(b"test").digest()).decode(),
    }
    responses = await asyncio.gather(
        *[command(api, owner, "POST", "/api/v1/media/uploads", payload) for _ in range(6)]
    )
    assert sorted(r.status_code for r in responses) == [201, 201, 201, 201, 201, 409], [
        r.text for r in responses
    ]
    assert (
        await command(
            api, owner, "POST", "/api/v1/media/uploads", {**payload, "size_bytes": 25_000_001}
        )
    ).status_code == 413
    assert (
        await command(
            api,
            owner,
            "POST",
            "/api/v1/media/uploads",
            {**payload, "content_type": "application/x-msdownload"},
        )
    ).status_code == 422
    uploaded = next(response.json() for response in responses if response.status_code == 201)
    ok(
        await api.delete(
            f"/api/v1/media/uploads/{uploaded['upload_id']}", headers=owner["headers"]
        ),
        204,
    )
    for _ in range(100):
        replacement = await command(api, owner, "POST", "/api/v1/media/uploads", payload)
        if replacement.status_code == 201:
            break
        assert replacement.status_code == 409
        await asyncio.sleep(0.1)
    assert replacement.status_code == 201, (
        "Cancelled upload did not release its project reservation"
    )


async def test_account_deletion_transfers_to_earliest_member(api, users):
    owner, first, second = users
    row = await project(api, owner)
    for candidate in [first, second]:
        application = ok(
            await command(
                api,
                candidate,
                "POST",
                f"/api/v1/projects/{row['id']}/applications",
                {"message": "Хочу участвовать в исследовании"},
            ),
            201,
        )
        ok(
            await command(
                api,
                owner,
                "POST",
                f"/api/v1/projects/{row['id']}/applications/{application['id']}/accept",
                {},
            )
        )
    job = ok(
        await command(
            api,
            owner,
            "DELETE",
            "/api/v1/account/me",
            {"current_password": owner["password"], "confirm": True},
        ),
        202,
    )
    assert (await api.get("/api/v1/account/me", headers=owner["headers"])).status_code in {401, 403}
    for _ in range(200):
        status = ok(
            await api.get(
                f"/api/v1/account/deletion/{job['deletion_id']}",
                headers={"Authorization": "Bearer " + job["operation_token"]},
            )
        )
        if status["status"] == "completed":
            break
        await asyncio.sleep(0.1)
    assert status["status"] == "completed", status
    result = ok(await api.get(f"/api/v1/projects/{row['id']}", headers=first["headers"]))
    assert result["owner_id"] == first["id"]
    assert result["creator_name"] == "Тест Участник0"
    assert (await api.get(f"/api/v1/users/{owner['id']}")).status_code == 404


@pytest.mark.parametrize("kind", ["users", "projects", "papers"])
async def test_invalid_uuid_has_error_envelope(api, kind):
    response = await api.get(f"/api/v1/{kind}/invalid-uuid")
    assert response.status_code in {404, 422}
    assert response.json()["error"]["code"]
