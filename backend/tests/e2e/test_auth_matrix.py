import asyncio
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.e2e

PUBLIC_GET = re.compile(
    r"^/api/v1/(users(?:/[^/]+(?:/avatar-url)?)?|"
    r"projects(?:/[^/]+(?:/members|/files(?:/[^/]+/file-url)?)?)?|"
    r"papers(?:/[^/]+(?:/file-url)?)?|dictionaries/[^/]+)$"
)
PUBLIC_AUTH = {
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
    "/api/v1/auth/password/forgot",
    "/api/v1/auth/password/reset",
    "/api/v1/auth/2fa/challenge",
}


ROOT = Path(__file__).resolve().parents[3]


def contracts():
    return [
        json.loads(path.read_text()) for path in sorted((ROOT / "contracts/openapi").glob("*.json"))
    ]


async def test_every_declared_protected_endpoint_requires_credentials(api):
    checked = []
    for schema in await asyncio.to_thread(contracts):
        for template, methods in schema["paths"].items():
            if not template.startswith("/api/v1/") or template in PUBLIC_AUTH:
                continue
            for method in methods:
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                endpoint = re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), template)
                if method == "get" and PUBLIC_GET.fullmatch(endpoint):
                    continue
                response = await api.request(method, endpoint, json={} if method != "get" else None)
                assert response.status_code == 401, (
                    method,
                    template,
                    response.status_code,
                    response.text[:300],
                )
                assert response.json()["error"]["code"] in {"AUTH_REQUIRED", "INVALID_TOKEN"}
                checked.append({"method": method, "path": template, "status": 401})
                await asyncio.sleep(0.06)
    assert len(checked) >= 60
    await asyncio.to_thread(
        (ROOT / "test-results/auth-matrix.json").write_text, json.dumps(checked, indent=2)
    )
