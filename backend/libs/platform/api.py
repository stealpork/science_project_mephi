import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from functools import lru_cache
from typing import ClassVar
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator
from redis.asyncio import Redis
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException

from libs.platform.config import get_settings
from libs.platform.database import Database, now, uid
from libs.platform.errors import APIError
from libs.platform.schemas import ErrorEnvelope

logger = logging.getLogger("science")
logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def command_schema(schema: dict, model):
    for field in model.non_nullable_fields:
        property_schema = schema.get("properties", {}).get(field, {})
        variants = property_schema.get("anyOf", [])
        if variants:
            variants = [item for item in variants if item.get("type") != "null"]
            if len(variants) == 1:
                property_schema.pop("anyOf")
                property_schema.update(variants[0])
            else:
                property_schema["anyOf"] = variants
        if property_schema.get("default") is None:
            property_schema.pop("default", None)


class Command(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, json_schema_extra=command_schema
    )
    non_nullable_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def identifiers_are_uuids(cls, values):
        if isinstance(values, dict):
            for key, value in values.items():
                if value is None:
                    if key in cls.non_nullable_fields:
                        raise ValueError(f"{key} cannot be null; omit it to leave it unchanged")
                    continue
                if key == "id" or key.endswith("_id"):
                    values[key] = str(UUID(str(value)))
                elif key == "ids" or key.endswith("_ids"):
                    if isinstance(value, list):
                        values[key] = [str(UUID(str(item))) for item in value]
        return values


class UUIDRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request: Request):
            for name, value in request.path_params.items():
                if name.endswith("_id"):
                    try:
                        request.path_params[name] = str(UUID(str(value)))
                    except ValueError:
                        raise APIError(
                            422, "INVALID_IDENTIFIER", "Некорректный идентификатор"
                        ) from None
            return await handler(request)

        return checked


def create_app(service: str) -> FastAPI:
    settings = get_settings()
    if settings.service_name != service:
        raise RuntimeError("SC_SERVICE_NAME does not match application")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.db = Database(settings)
        app.state.http = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
        app.state.redis = Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2
        )
        app.state.proxy_cache = (0.0, set())
        yield
        await app.state.http.aclose()
        await app.state.redis.aclose()
        await app.state.db.engine.dispose()

    app = FastAPI(
        title=f"Stepork Science · {service}",
        version="0.1.0",
        lifespan=lifespan,
        responses={
            status: {"model": ErrorEnvelope}
            for status in [400, 401, 403, 404, 409, 413, 422, 429, 500, 503]
        },
    )
    app.router.route_class = UUIDRoute
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    )
    registry = CollectorRegistry()
    requests = Counter(
        "science_http_requests_total",
        "HTTP requests",
        ["service", "method", "route", "status"],
        registry=registry,
    )
    duration = Histogram(
        "science_http_duration_seconds", "Request duration", ["service", "route"], registry=registry
    )
    database_ready = Gauge(
        "science_database_ready", "Database readiness", ["service"], registry=registry
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        peer = request.client.host if request.client else "unknown"
        request.state.client_ip = peer
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            expires, addresses = request.app.state.proxy_cache
            if expires <= time.monotonic():
                addresses = set()
                for host in settings.trusted_proxy_hosts:
                    try:
                        results = await asyncio.get_running_loop().getaddrinfo(host, 80)
                        addresses.update(result[4][0] for result in results)
                    except OSError:
                        pass
                request.app.state.proxy_cache = (time.monotonic() + 5, addresses)
            if peer in addresses:
                try:
                    request.state.client_ip = str(ipaddress.ip_address(forwarded))
                except ValueError:
                    pass
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            incoming if 0 < len(incoming) <= 100 and incoming.replace("-", "").isalnum() else uid()
        )
        started = time.monotonic()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        elapsed = time.monotonic() - started
        requests.labels(service, request.method, route, response.status_code).inc()
        duration.labels(service, route).observe(elapsed)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "private, no-store"
        if route not in {"/health/live", "/health/ready", "/metrics"}:
            logger.info(
                json.dumps(
                    {
                        "service": service,
                        "route": route,
                        "status": response.status_code,
                        "duration_ms": round(elapsed * 1000),
                        "request_id": request.state.request_id,
                    }
                )
            )
        return response

    @app.exception_handler(APIError)
    async def domain_error(request: Request, exc: APIError):
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status,
            headers={"Retry-After": "5"} if exc.status in {429, 503} else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = [
            {
                "path": ".".join(map(str, error["loc"][1:])),
                "code": error["type"],
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return await domain_error(
            request, APIError(422, "VALIDATION_ERROR", "Проверьте поля запроса", {"fields": fields})
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return await domain_error(
            request, APIError(exc.status_code, "HTTP_ERROR", "Запрос не может быть выполнен")
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error(
            json.dumps(
                {
                    "service": service,
                    "error_type": type(exc).__name__,
                    "request_id": getattr(request.state, "request_id", "unknown"),
                }
            )
        )
        return await domain_error(request, APIError(500, "INTERNAL_ERROR", "Внутренняя ошибка"))

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError):
        return await domain_error(
            request,
            APIError(409, "CONFLICT", "Запись уже существует или конфликтует с текущими данными"),
        )

    @app.exception_handler(DataError)
    async def malformed_identifier(request: Request, exc: DataError):
        return await domain_error(
            request,
            APIError(422, "INVALID_IDENTIFIER", "Некорректный идентификатор или значение поля"),
        )

    @app.exception_handler(PoolTimeoutError)
    async def pool_busy(request: Request, exc: PoolTimeoutError):
        return await domain_error(
            request, APIError(503, "DATABASE_BUSY", "База данных временно занята")
        )

    @app.exception_handler(OSError)
    async def dependency_io_error(request: Request, exc: OSError):
        return await domain_error(
            request, APIError(503, "DEPENDENCY_UNAVAILABLE", "Инфраструктура временно недоступна")
        )

    @app.exception_handler(DBAPIError)
    async def database_error(request: Request, exc: DBAPIError):
        state = getattr(exc.orig, "sqlstate", "") or ""
        if state in {"57014", "55P03", "57P01", "57P02", "57P03"} or state.startswith("08"):
            return await domain_error(
                request, APIError(503, "DATABASE_BUSY", "Повторите запрос позже")
            )
        logger.error(
            json.dumps(
                {
                    "service": service,
                    "task": "database",
                    "error_type": type(exc).__name__,
                    "request_id": request.state.request_id,
                }
            )
        )
        return await domain_error(request, APIError(500, "INTERNAL_ERROR", "Внутренняя ошибка"))

    @app.get("/health/live", include_in_schema=False)
    async def live():
        return {"status": "ok", "service": service}

    @app.get("/health/ready", include_in_schema=False)
    async def ready(request: Request):
        try:
            async with request.app.state.db.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            database_ready.labels(service).set(1)
            return {"status": "ready", "service": service}
        except Exception:
            database_ready.labels(service).set(0)
            raise APIError(503, "DATABASE_UNAVAILABLE", "База данных недоступна") from None

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    if service != "identity":
        import importlib

        from libs.platform.deletion import install

        async def delete_resource_owner(request, db, user_id):
            handler = importlib.import_module(f"services.{service}.events").delete_user
            await handler(request, db, user_id)

        install(app, delete_resource_owner)

    return app


def dto(record, *, exclude: set[str] | None = None) -> dict:
    return jsonable_encoder(
        {
            c.name: getattr(record, c.name)
            for c in record.__table__.columns
            if c.name not in (exclude or set())
        }
    )


def assert_version(record, version: int) -> None:
    if record.version != version:
        raise APIError(
            409,
            "VERSION_CONFLICT",
            "Запись изменена. Обновите данные",
            {"current_version": record.version},
        )
    record.version += 1
    record.updated_at = now()


async def paginate(db, query, model, request: Request, limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise APIError(422, "INVALID_LIMIT", "Размер страницы от 1 до 100")
    sorts = {"newest": (model.created_at, False), "oldest": (model.created_at, True)}
    heading = getattr(model, "title", None)
    if model.__tablename__ == "profiles":
        heading = model.first_name + " " + model.last_name
        sorts.update(name=(func.lower(heading), True), recently_active=(model.updated_at, False))
    if model.__tablename__ == "projects":
        sorts["deadline"] = (model.deadline, True)
    if model.__tablename__ == "research_papers":
        sorts.update(
            year_desc=(model.year, False),
            most_viewed=(model.views_count, False),
            most_saved=(model.saves_count, False),
        )
    if heading is not None:
        search = request.query_params.get("q", "")
        sorts["relevance"] = (
            (case((heading.ilike(search), 3), (heading.ilike(search + "%"), 2), else_=1), False)
            if search
            else (model.created_at, False)
        )
    sort = request.query_params.get("sort", "newest")
    if sort not in sorts:
        raise APIError(422, "INVALID_SORT", "Неизвестная сортировка")
    expression, ascending = sorts[sort]
    filters = sorted(
        (k, v) for k, v in request.query_params.multi_items() if k not in {"cursor", "limit"}
    )
    identity = getattr(request.state, "user_id", "public")
    fingerprint = hashlib.sha256(
        json.dumps([request.url.path, identity, filters]).encode()
    ).hexdigest()[:16]
    cursor_key = hashlib.sha256(
        (request.app.state.settings.service_token + ":cursor-v1").encode()
    ).digest()
    cursor_cipher = Fernet(base64.urlsafe_b64encode(cursor_key))
    cursor = request.query_params.get("cursor")
    if cursor:
        try:
            if len(cursor) > 4096:
                raise ValueError("Cursor too long")
            data = json.loads(cursor_cipher.decrypt(cursor.encode(), ttl=86400))
            if data["filter"] != fingerprint:
                raise ValueError("Cursor filter mismatch")
            at = datetime.fromisoformat(data["at"])
            identifier = str(UUID(data["id"]))
            value = data["value"]
            if value is not None and (
                sort in {"newest", "oldest", "deadline", "recently_active"}
                or (sort == "relevance" and not request.query_params.get("q"))
            ):
                value = datetime.fromisoformat(value)
            tie = or_(model.created_at < at, and_(model.created_at == at, model.id < identifier))
            if value is None:
                boundary = and_(expression.is_(None), tie)
            else:
                after = expression > value if ascending else expression < value
                boundary = or_(after, expression.is_(None), and_(expression == value, tie))
            query = query.where(boundary)
        except (ValueError, TypeError, KeyError, InvalidToken, json.JSONDecodeError):
            raise APIError(422, "INVALID_CURSOR", "Некорректный cursor") from None
    rows = list(
        (
            await db.execute(
                query.add_columns(expression.label("cursor_sort_value"))
                .order_by(
                    (expression.asc() if ascending else expression.desc()).nulls_last(),
                    model.created_at.desc(),
                    model.id.desc(),
                )
                .limit(limit + 1)
            )
        ).all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        last, value = rows[-1][0], rows[-1][-1]
        next_cursor = cursor_cipher.encrypt(
            json.dumps(
                {
                    "at": last.created_at.isoformat(),
                    "id": last.id,
                    "filter": fingerprint,
                    "value": jsonable_encoder(value),
                }
            ).encode()
        ).decode()
    return {"items": [dto(row[0]) for row in rows], "next_cursor": next_cursor, "has_more": more}


async def command(request: Request, db, operation, status: int = 200):
    key = request.headers.get("Idempotency-Key")
    if not key or len(key) > 100:
        raise APIError(422, "IDEMPOTENCY_KEY_REQUIRED", "Передайте Idempotency-Key")
    scope = f"{getattr(request.state, 'user_id', 'anonymous')}:{request.method}:{request.url.path}:{key}"
    body_hash = hashlib.sha256(await request.body()).hexdigest()

    lock = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
    table = request.app.state.db.metadata.tables["idempotency_keys"]
    previous = (await db.execute(select(table).where(table.c.scope == scope))).mappings().first()
    if previous and previous["created_at"] > now() - timedelta(hours=24):
        if previous["request_hash"] != body_hash:
            raise APIError(409, "IDEMPOTENCY_CONFLICT", "Ключ уже использован с другим запросом")
        return JSONResponse(previous["response"], status_code=previous["status"])
    if previous:
        await db.execute(table.delete().where(table.c.scope == scope))
    result = jsonable_encoder(await operation())
    model = getattr(request.scope.get("route"), "response_model", None)
    if model is not None:
        adapter = response_adapter(model)
        result = adapter.dump_python(adapter.validate_python(result), mode="json")
    await db.execute(
        table.insert().values(scope=scope, request_hash=body_hash, response=result, status=status)
    )
    return JSONResponse(result, status_code=status)


@lru_cache(maxsize=128)
def response_adapter(model):
    return TypeAdapter(model)


async def rate_limit(request: Request, name: str, limit: int = 20, seconds: int = 60) -> None:
    address = getattr(
        request.state, "client_ip", request.client.host if request.client else "unknown"
    )
    key = f"rate:{name}:{address}"
    script = "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n"
    try:
        count = await request.app.state.redis.eval(script, 1, key, seconds)
    except Exception:
        raise APIError(503, "RATE_LIMIT_UNAVAILABLE", "Сервис временно недоступен") from None
    if count > limit:
        raise APIError(429, "RATE_LIMITED", "Слишком много запросов")
