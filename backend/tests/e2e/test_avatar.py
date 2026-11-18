import asyncio
import base64
import hashlib
import io

import httpx
import pytest
from PIL import Image

from tests.conftest import command, ok

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("users", [1], indirect=True)
async def test_avatar_upload_reencode_bind_and_guest_download(api, users):
    user = users[0]
    buffer = io.BytesIO()
    Image.new("RGB", (30, 40), "white").save(buffer, "JPEG", comment=b"must not be exposed")
    data = buffer.getvalue()
    upload = ok(
        await command(
            api,
            user,
            "POST",
            "/api/v1/media/uploads",
            {
                "purpose": "avatar",
                "resource_id": user["id"],
                "filename": "avatar.jpg",
                "content_type": "image/jpeg",
                "size_bytes": len(data),
                "checksum_sha256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
            },
        ),
        201,
    )
    async with httpx.AsyncClient() as s3:
        response = await s3.put(
            upload["upload_url"], content=data, headers=upload["required_headers"]
        )
        assert response.status_code == 200
    ok(
        await command(api, user, "POST", f"/api/v1/media/uploads/{upload['upload_id']}/complete"),
        202,
    )
    for _ in range(100):
        state = ok(
            await api.get(f"/api/v1/media/uploads/{upload['upload_id']}", headers=user["headers"])
        )
        if state["status"] in {"ready", "rejected"}:
            break
        await asyncio.sleep(0.1)
    assert state["status"] == "ready", state
    profile = ok(await api.get("/api/v1/me/profile", headers=user["headers"]))
    ok(
        await api.patch(
            "/api/v1/me/profile",
            headers=user["headers"],
            json={"version": profile["version"], "avatar_file_id": upload["file_id"]},
        )
    )
    response = await api.get(f"/api/v1/users/{user['id']}/avatar-url")
    assert "no-store" in response.headers["cache-control"]
    link = ok(response)
    async with httpx.AsyncClient() as s3:
        result = await s3.get(link["url"])
        assert result.status_code == 200
        with Image.open(io.BytesIO(result.content)) as image:
            assert image.format == "PNG" and image.size == (30, 40)
            assert "comment" not in image.info
