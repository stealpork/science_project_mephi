import hashlib
import hmac
from collections.abc import Callable
from typing import Any

import httpx
import jwt
from fastapi import Header, Request

from libs.platform.config import SERVICES, get_settings
from libs.platform.errors import APIError


async def internal_call(request: Request, service: str, method: str, path: str, **kwargs) -> Any:
    settings = request.app.state.settings
    try:
        response = await request.app.state.http.request(
            method,
            settings.service_url(service) + path,
            headers={
                "X-Service-Name": settings.service_name,
                "X-Service-Token": settings.service_token,
                "X-Request-ID": request.state.request_id,
            },
            **kwargs,
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise APIError(
            503, "DEPENDENCY_UNAVAILABLE", f"Сервис {service} временно недоступен"
        ) from exc
    if response.status_code >= 400:
        try:
            error = response.json()["error"]
            if not isinstance(error["code"], str) or not isinstance(error["message"], str):
                raise ValueError("Malformed dependency error")
            raise APIError(
                response.status_code, error["code"], error["message"], error.get("details")
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise APIError(
                503, "DEPENDENCY_UNAVAILABLE", f"Сервис {service} временно недоступен"
            ) from exc
    if not 200 <= response.status_code < 300:
        raise APIError(503, "DEPENDENCY_UNAVAILABLE", f"Сервис {service} временно недоступен")
    try:
        return response.json() if response.content else None
    except ValueError as exc:
        raise APIError(
            503, "DEPENDENCY_UNAVAILABLE", f"Сервис {service} временно недоступен"
        ) from exc


def internal(*allowed: str) -> Callable:
    async def dependency(
        request: Request,
        x_service_name: str = Header(default=""),
        x_service_token: str = Header(default=""),
    ) -> str:
        expected = request.app.state.settings.internal_token_hashes.get(x_service_name, "")
        actual = hashlib.sha256(x_service_token.encode()).hexdigest()
        if (
            not expected
            or not hmac.compare_digest(actual, expected)
            or x_service_name not in (allowed or SERVICES)
        ):
            raise APIError(403, "SERVICE_FORBIDDEN", "Нет доступа к внутреннему API")
        return x_service_name

    return dependency


def decode_token(token: str, settings, token_type: str = "access") -> dict:
    try:
        identifier = jwt.get_unverified_header(token).get("kid")
        if not isinstance(identifier, str):
            raise jwt.InvalidTokenError("Missing signing key identifier")
        if identifier == settings.jwt_key_id:
            public_key = settings.jwt_public_key_file.read_text()
        else:
            public_key = (settings.jwt_additional_public_keys or {}).get(identifier)
        if not public_key:
            raise jwt.InvalidTokenError("Unknown signing key")
        required = ["sub", "iat", "exp", "jti", "token_type"]
        if token_type in {"access", "refresh"}:
            required.append("sid")
        elif token_type == "deletion_status":
            required.append("deletion_id")
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["EdDSA"],
            issuer=settings.jwt_issuer,
            audience={
                "access": "science-api",
                "refresh": "science-refresh",
                "deletion_status": "science-deletion",
                "mfa_challenge": "science-mfa",
            }[token_type],
            options={"require": required},
        )
        if claims["token_type"] != token_type:
            raise jwt.InvalidTokenError("Wrong token type")
        return claims
    except jwt.ExpiredSignatureError as exc:
        raise APIError(401, "TOKEN_EXPIRED", "Срок действия токена истёк") from exc
    except (jwt.InvalidTokenError, OSError, KeyError) as exc:
        raise APIError(401, "INVALID_TOKEN", "Недействительный токен") from exc


async def current_user(request: Request, authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise APIError(401, "AUTH_REQUIRED", "Необходимо войти")
    token = authorization[7:]
    claims = decode_token(token, request.app.state.settings)
    request.state.user_id = claims["sub"]
    if request.app.state.settings.service_name == "identity":
        from services.identity.auth import introspect

        async with request.app.state.db.sessions() as db:
            return await introspect(db, claims)
    return await internal_call(
        request, "identity", "POST", "/internal/v1/introspect", json={"token": token}
    )


async def optional_user(request: Request, authorization: str = Header(default="")) -> dict | None:
    return await current_user(request, authorization) if authorization else None


def moderator(user: dict) -> dict:
    if not set(user["roles"]) & {"moderator", "admin"}:
        raise APIError(403, "FORBIDDEN", "Требуются права модератора")
    settings = get_settings()
    if settings.admin_2fa_required and not user.get("mfa_verified"):
        raise APIError(403, "MFA_REQUIRED", "Требуется второй фактор")
    return user


def admin(user: dict) -> dict:
    moderator(user)
    if "admin" not in user["roles"]:
        raise APIError(403, "FORBIDDEN", "Требуются права администратора")
    return user


async def enrich_favorites(request: Request, kind: str, user: dict | None, cards: list[dict]):
    if not user:
        for card in cards:
            card["is_favorited"] = False
        return cards
    try:
        saved = set()
        for offset in range(0, len(cards), 100):
            batch = cards[offset : offset + 100]
            result = await internal_call(
                request,
                "activity",
                "POST",
                "/internal/v1/favorites/batch",
                json={"user_id": user["id"], "type": kind, "ids": [card["id"] for card in batch]},
            )
            saved.update(result["saved_ids"])
        for card in cards:
            card["is_favorited"] = card["id"] in saved
    except APIError as exc:
        if exc.status != 503:
            raise
        for card in cards:
            card["is_favorited"] = None
    return cards
