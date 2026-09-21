import asyncio
import hashlib
import secrets
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select

from libs.platform.database import now, uid
from libs.platform.errors import APIError
from services.identity.models import Session, User

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
password_capacity = asyncio.Semaphore(2)
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def hash_password(value: str) -> str:
    async with password_capacity:
        return await asyncio.to_thread(password_hasher.hash, value)


async def verify_password(hashed: str, value: str) -> bool:
    try:
        async with password_capacity:
            return await asyncio.to_thread(password_hasher.verify, hashed, value)
    except (VerificationError, InvalidHashError):
        return False


def token(settings, user_id: str, kind: str, ttl: int, **extra) -> str:
    issued = now()
    return jwt.encode(
        {
            "sub": user_id,
            "jti": uid(),
            "iss": settings.jwt_issuer,
            "aud": {
                "access": "science-api",
                "refresh": "science-refresh",
                "deletion_status": "science-deletion",
                "mfa_challenge": "science-mfa",
            }[kind],
            "token_type": kind,
            "iat": issued,
            "exp": issued + timedelta(seconds=ttl),
            **extra,
        },
        settings.jwt_private_key_file.read_text(),
        algorithm="EdDSA",
        headers={"kid": settings.jwt_key_id},
    )


def account(user: User) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "email_verified": user.email_verified,
        "global_roles": user.roles,
        "account_status": user.status,
    }


async def introspect(db, claims: dict) -> dict:
    user = await db.get(User, claims["sub"])
    session = await db.get(Session, claims.get("sid")) if claims.get("sid") else None
    if (
        not user
        or not session
        or session.user_id != user.id
        or session.revoked_at
        or session.expires_at <= now()
    ):
        raise APIError(401, "SESSION_REVOKED", "Сессия завершена")
    if user.status != "active":
        raise APIError(
            403,
            "ACCOUNT_BLOCKED" if user.status == "blocked" else "ACCOUNT_UNAVAILABLE",
            "Учётная запись недоступна",
        )
    return {
        "id": user.id,
        "roles": user.roles,
        "email_verified": user.email_verified,
        "sid": session.id,
        "mfa_verified": session.mfa_verified,
    }


def issue_pair(settings, user: User, session: Session) -> dict:
    refresh = token(
        settings,
        user.id,
        "refresh",
        max(1, int((session.expires_at - now()).total_seconds())),
        sid=session.id,
    )
    session.refresh_hash = digest(refresh)
    return {
        "access_token": token(
            settings, user.id, "access", settings.access_ttl_seconds, sid=session.id
        ),
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_ttl_seconds,
        "refresh_expires_in": max(0, int((session.expires_at - now()).total_seconds())),
        "account": account(user),
    }


async def new_session(db, settings, user: User, user_agent: str, mfa_verified=False) -> dict:
    session = Session(
        id=uid(),
        user_id=user.id,
        refresh_hash="pending",
        expires_at=now() + timedelta(seconds=settings.refresh_ttl_seconds),
        user_agent=user_agent[:300],
        mfa_verified=mfa_verified,
    )
    db.add(session)
    return issue_pair(settings, user, session)


async def check_user(db, email: str, password: str) -> User:
    user = await db.scalar(select(User).where(User.email == email.lower()))

    hashed = user.password_hash if user else dummy_password_hash
    valid = await verify_password(hashed, password)
    if not user or not valid:
        raise APIError(401, "INVALID_CREDENTIALS", "Неверный email или пароль")
    if user.status != "active":
        raise APIError(403, "ACCOUNT_BLOCKED", "Учётная запись недоступна")
    return user
