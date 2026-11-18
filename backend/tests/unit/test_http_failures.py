from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from libs.platform import http as gateway
from libs.platform.errors import APIError

pytestmark = pytest.mark.unit


def context(client):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    service_name="projects",
                    service_token="test-only",
                    service_url=lambda service: f"http://{service}",
                ),
                http=client,
            )
        ),
        state=SimpleNamespace(request_id="trace-test"),
    )


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (403, {"error": {"code": "DENIED", "message": "Forbidden"}}, "DENIED"),
        (
            404,
            {"error": {"code": "MISSING", "message": "Not found", "details": {"x": 1}}},
            "MISSING",
        ),
        (502, {"wrong": "envelope"}, "DEPENDENCY_UNAVAILABLE"),
        (500, [], "DEPENDENCY_UNAVAILABLE"),
        (503, None, "DEPENDENCY_UNAVAILABLE"),
        (500, {"error": {"code": None, "message": []}}, "DEPENDENCY_UNAVAILABLE"),
    ],
)
async def test_internal_error_mapping(status, body, expected):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    ) as client:
        with pytest.raises(APIError) as caught:
            await gateway.internal_call(context(client), "people", "GET", "/test")
    assert caught.value.code == expected
    assert caught.value.status == (status if expected != "DEPENDENCY_UNAVAILABLE" else 503)


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
async def test_internal_transport_failure_is_retryable(error):
    def failed(request):
        raise error("private host details must not escape", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(APIError) as caught:
            await gateway.internal_call(context(client), "people", "GET", "/test")
    assert caught.value.status == 503
    assert "private host" not in caught.value.message


@pytest.mark.parametrize("status,expected", [(200, {"ok": True}), (204, None)])
async def test_internal_success_and_trace_headers(status, expected):
    def respond(request):
        assert request.headers["X-Service-Name"] == "projects"
        assert request.headers["X-Service-Token"] == "test-only"
        assert request.headers["X-Request-ID"] == "trace-test"
        return httpx.Response(status, json=expected) if status == 200 else httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await gateway.internal_call(context(client), "people", "POST", "/test") == expected


@pytest.mark.parametrize(
    "status,content", [(200, b"not-json"), (302, b"redirect"), (503, b"html error")]
)
async def test_dependency_protocol_errors_are_retryable_not_unhandled(monkeypatch, status, content):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=content))
    ) as client:
        with pytest.raises(APIError) as caught:
            await gateway.internal_call(context(client), "people", "GET", "/test")
    assert caught.value.status == 503


@pytest.mark.parametrize("status", [403, 404, 503])
async def test_favorite_failure_never_falsely_reports_unsaved(monkeypatch, status):
    monkeypatch.setattr(
        gateway, "internal_call", AsyncMock(side_effect=APIError(status, "FAILED", "error"))
    )
    cards = [{"id": "one"}]
    if status == 503:
        assert await gateway.enrich_favorites(None, "paper", {"id": "actor"}, cards) == [
            {"id": "one", "is_favorited": None}
        ]
    else:
        with pytest.raises(APIError):
            await gateway.enrich_favorites(None, "paper", {"id": "actor"}, cards)


async def test_favorites_are_batched_and_guest_does_not_call_service(monkeypatch):
    call = AsyncMock(side_effect=[{"saved_ids": ["0", "99"]}, {"saved_ids": ["100"]}])
    monkeypatch.setattr(gateway, "internal_call", call)
    cards = [{"id": str(number)} for number in range(101)]
    await gateway.enrich_favorites(None, "paper", {"id": "actor"}, cards)
    assert call.await_count == 2
    assert [c["id"] for c in cards if c["is_favorited"]] == ["0", "99", "100"]
    call.reset_mock()
    await gateway.enrich_favorites(None, "paper", None, cards)
    assert not any(c["is_favorited"] for c in cards)
    call.assert_not_awaited()


async def test_optional_authorization_never_swallows_invalid_credentials(monkeypatch):
    function = AsyncMock(side_effect=APIError(401, "INVALID_TOKEN", "invalid"))
    monkeypatch.setattr(gateway, "current_user", function)
    assert await gateway.optional_user(None, "") is None
    function.assert_not_awaited()
    with pytest.raises(APIError):
        await gateway.optional_user(None, "Bearer invalid")
