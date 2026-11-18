import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

from starlette.requests import Request

from libs.platform import api
from libs.platform.config import Settings


def service(monkeypatch, name):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(service_name=name))
    return importlib.import_module(f"services.{name}.main")


def rows(values):
    return Obj(all=lambda: values)


@asynccontextmanager
async def context(value=None):
    yield value


def database():
    return Obj(
        scalar=AsyncMock(return_value=None),
        scalars=AsyncMock(return_value=rows([])),
        get=AsyncMock(return_value=None),
        execute=AsyncMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        delete=AsyncMock(),
        add=MagicMock(),
        info={},
        begin=lambda: context(),
    )


def request(settings=None, path="/api/test", method="POST", query="", body=b"{}", headers=None):
    async def receive():
        return {"type": "http.request", "body": body}

    app = Obj(state=Obj(settings=settings or Settings(), redis=Obj(eval=AsyncMock(return_value=1))))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
        "scheme": "http",
        "app": app,
    }
    result = Request(scope, receive)
    result.state.request_id, result.state.user_id = "trace", "viewer"
    return result


async def operation(request, db, callback, status=200):
    return await callback()


def endpoint(app, name):
    return next(route.endpoint for route in app.routes if route.name == name)
