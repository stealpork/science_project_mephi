import secrets
from datetime import timedelta
from typing import Annotated

import pyotp
from cryptography.fernet import Fernet
from fastapi import Depends, Header, Request
from pydantic import EmailStr, Field, StringConstraints
from sqlalchemy import select, update

from libs.platform.api import Command, command, create_app, dto, paginate, rate_limit
from libs.platform.database import append_event, now, session, uid
from libs.platform.errors import APIError, require
from libs.platform.http import current_user, decode_token, internal, internal_call
from libs.platform.schemas import AccountDTO, MFARequired, TokenPair
from services.identity.auth import (
    account,
    check_user,
    digest,
    hash_password,
    introspect,
    issue_pair,
    new_session,
    token,
    verify_password,
)
from services.identity.models import AccountDeletion, SecurityAudit, SecurityToken, Session, User

app = create_app("identity")
Password = Annotated[str, StringConstraints(strip_whitespace=False)]


class Register(Command):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    password: Password = Field(min_length=10, max_length=128)
    university_id: str | None = None
    faculty_id: str | None = None
    specialty_id: str | None = None
    course: int | None = Field(default=None, ge=1, le=8)


class Login(Command):
    email: EmailStr
    password: Password = Field(max_length=128)


class Refresh(Command):
    refresh_token: str = Field(max_length=8000)


class TokenInput(Command):
    token: str = Field(max_length=8000)


class EmailInput(Command):
    email: EmailStr


class PasswordReset(TokenInput):
    new_password: Password = Field(min_length=10, max_length=128)


class PasswordChange(Command):
    current_password: Password = Field(max_length=128)
    new_password: Password = Field(min_length=10, max_length=128)


class PasswordInput(Command):
    current_password: Password = Field(max_length=128)


class CodeInput(Command):
    code: str = Field(min_length=6, max_length=30)


class MFADisable(PasswordInput, CodeInput):
    pass


class MFAChallenge(Command):
    challenge_token: str = Field(max_length=8000)
    code: str | None = Field(default=None, max_length=30)
    recovery_code: str | None = Field(default=None, max_length=100)


def cipher(request: Request) -> Fernet:
    return Fernet(request.app.state.settings.mfa_encryption_key.encode())


async def send_token(request, db, user: User, kind: str) -> None:
    secret = secrets.token_urlsafe(32)
    db.add(
        SecurityToken(
            user_id=user.id,
            kind=kind,
            token_hash=digest(secret),
            expires_at=now() + timedelta(minutes=30),
        )
    )
    await append_event(
        db, "identity.mail_requested", user.id, {"email": user.email, "kind": kind, "token": secret}
    )


async def consume_token(db, secret: str, kind: str) -> User:
    row = await db.scalar(
        select(SecurityToken)
        .where(SecurityToken.token_hash == digest(secret), SecurityToken.kind == kind)
        .with_for_update()
    )
    if not row or row.consumed_at or row.expires_at <= now():
        raise APIError(400, "INVALID_ACTION_TOKEN", "Ссылка недействительна или истекла")
    user = await db.get(User, row.user_id)
    require(
        user is not None and user.status == "active",
        "ACCOUNT_UNAVAILABLE",
        "Учётная запись недоступна",
        403,
    )
    row.consumed_at = now()
    return user


@app.post("/api/v1/auth/register", status_code=201)
async def register(body: Register, request: Request, db=Depends(session, scope="function")):
    await rate_limit(request, "register", 20)
    selected = {
        field: getattr(body, field)
        for field in ("university_id", "faculty_id", "specialty_id")
        if getattr(body, field)
    }
    if selected:
        references = await internal_call(
            request,
            "people",
            "POST",
            "/internal/v1/dictionaries/validate",
            json={"ids": list(set(selected.values()))},
        )
        entries = {entry["id"]: entry for entry in references["items"]}
        for field, kind in {
            "university_id": "universities",
            "faculty_id": "faculties",
            "specialty_id": "specialties",
        }.items():
            require(
                field not in selected or entries[selected[field]]["kind"] == kind,
                "INVALID_DICTIONARY_KIND",
                "Неверный тип значения справочника",
                422,
            )
        if body.faculty_id:
            require(
                entries[body.faculty_id]["parent_id"] == body.university_id,
                "INVALID_FACULTY",
                "Факультет не принадлежит университету",
                422,
            )
    email = str(body.email).lower()
    require(
        not await db.scalar(select(User.id).where(User.email == email)),
        "EMAIL_ALREADY_EXISTS",
        "Email уже зарегистрирован",
    )
    user = User(id=uid(), email=email, password_hash=await hash_password(body.password))
    db.add(user)
    await db.flush()
    await append_event(
        db,
        "identity.user_registered",
        user.id,
        {"user_id": user.id, **body.model_dump(exclude={"password", "email"})},
    )
    await send_token(request, db, user, "verify_email")
    return {
        "user_id": user.id,
        "email_verification_required": request.app.state.settings.email_verification_required,
        "profile_status": "initializing",
    }


@app.post("/api/v1/auth/login", response_model=TokenPair | MFARequired)
async def login(body: Login, request: Request, db=Depends(session, scope="function")):
    await rate_limit(request, "login", 100)
    user = await check_user(db, str(body.email), body.password)
    if user.mfa_secret:
        challenge = token(request.app.state.settings, user.id, "mfa_challenge", 300)
        db.add(
            SecurityToken(
                user_id=user.id,
                kind="mfa",
                token_hash=digest(challenge),
                expires_at=now() + timedelta(minutes=5),
            )
        )
        return {"requires_2fa": True, "challenge_token": challenge, "expires_in": 300}
    if request.app.state.settings.admin_2fa_required and set(user.roles) & {"admin", "moderator"}:
        raise APIError(
            403, "MFA_SETUP_REQUIRED", "Сначала настройте второй фактор через служебную процедуру"
        )
    return await new_session(
        db, request.app.state.settings, user, request.headers.get("User-Agent", "")
    )


@app.post("/api/v1/auth/refresh", response_model=TokenPair)
async def refresh(body: Refresh, request: Request, db=Depends(session, scope="function")):
    await rate_limit(request, "refresh", 200)
    claims = decode_token(body.refresh_token, request.app.state.settings, "refresh")
    row = await db.scalar(
        select(Session)
        .where(Session.id == claims.get("sid"), Session.user_id == claims["sub"])
        .with_for_update()
    )
    require(
        row is not None and not row.revoked_at and row.expires_at > now(),
        "SESSION_REVOKED",
        "Сессия завершена",
        401,
    )
    if row.refresh_hash != digest(body.refresh_token):
        row.revoked_at = now()
        await db.commit()
        raise APIError(401, "REFRESH_REUSED", "Повторное использование токена. Войдите заново")
    user = await db.get(User, row.user_id)
    require(user.status == "active", "ACCOUNT_BLOCKED", "Учётная запись недоступна", 403)
    return issue_pair(request.app.state.settings, user, row)


@app.post("/api/v1/auth/logout", status_code=204)
async def logout(user=Depends(current_user), db=Depends(session, scope="function")):
    row = await db.get(Session, user["sid"])
    row.revoked_at = now()


@app.get("/api/v1/account/me", response_model=AccountDTO)
async def me(user=Depends(current_user), db=Depends(session, scope="function")):
    return account(await db.get(User, user["id"]))


@app.get("/api/v1/account/sessions")
async def sessions(user=Depends(current_user), db=Depends(session, scope="function")):
    rows = (
        await db.scalars(
            select(Session).where(
                Session.user_id == user["id"],
                Session.revoked_at.is_(None),
                Session.expires_at > now(),
            )
        )
    ).all()
    return {
        "items": [dto(row, exclude={"refresh_hash"}) for row in rows],
        "next_cursor": None,
        "has_more": False,
    }


@app.delete("/api/v1/account/sessions/{session_id}", status_code=204)
async def revoke(
    session_id: str, user=Depends(current_user), db=Depends(session, scope="function")
):
    row = await db.get(Session, session_id)
    require(row is not None and row.user_id == user["id"], "NOT_FOUND", "Сессия не найдена", 404)
    row.revoked_at = now()


@app.post("/api/v1/auth/verify-email")
async def verify_email(body: TokenInput, db=Depends(session, scope="function")):
    user = await consume_token(db, body.token, "verify_email")
    user.email_verified = True
    return {"verified": True}


@app.post("/api/v1/auth/resend-verification", status_code=202)
async def resend(body: EmailInput, request: Request, db=Depends(session, scope="function")):
    await rate_limit(request, "email", 10)
    user = await db.scalar(
        select(User).where(User.email == str(body.email).lower(), User.status == "active")
    )
    if user and not user.email_verified:
        await send_token(request, db, user, "verify_email")
    return {"status": "accepted"}


@app.post("/api/v1/auth/password/forgot", status_code=202)
async def forgot(body: EmailInput, request: Request, db=Depends(session, scope="function")):
    await rate_limit(request, "email", 10)
    user = await db.scalar(
        select(User).where(User.email == str(body.email).lower(), User.status == "active")
    )
    if user:
        await send_token(request, db, user, "reset_password")
    return {"status": "accepted"}


@app.post("/api/v1/auth/password/reset")
async def reset_password(body: PasswordReset, db=Depends(session, scope="function")):
    user = await consume_token(db, body.token, "reset_password")
    user.password_hash = await hash_password(body.new_password)
    await db.execute(
        update(SecurityToken)
        .where(
            SecurityToken.user_id == user.id,
            SecurityToken.kind == "reset_password",
            SecurityToken.consumed_at.is_(None),
        )
        .values(consumed_at=now())
    )
    await db.execute(update(Session).where(Session.user_id == user.id).values(revoked_at=now()))
    return {"status": "changed"}


@app.post("/api/v1/account/password/change")
async def change_password(
    body: PasswordChange, user=Depends(current_user), db=Depends(session, scope="function")
):
    row = await db.get(User, user["id"])
    require(
        await verify_password(row.password_hash, body.current_password),
        "INVALID_PASSWORD",
        "Неверный пароль",
        403,
    )
    row.password_hash = await hash_password(body.new_password)
    await db.execute(
        update(Session)
        .where(Session.user_id == user["id"], Session.id != user["sid"])
        .values(revoked_at=now())
    )
    return {"status": "changed"}


@app.post("/api/v1/account/2fa/setup")
async def mfa_setup(
    body: PasswordInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    row = await db.get(User, user["id"])
    require(
        await verify_password(row.password_hash, body.current_password),
        "INVALID_PASSWORD",
        "Неверный пароль",
        403,
    )
    secret = pyotp.random_base32()
    row.mfa_pending_secret = cipher(request).encrypt(secret.encode()).decode()
    return {
        "provisioning_uri": pyotp.TOTP(secret).provisioning_uri(
            name=row.email, issuer_name="Stepork Science"
        )
    }


@app.post("/api/v1/account/2fa/enable")
async def mfa_enable(
    body: CodeInput,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    row = await db.scalar(select(User).where(User.id == user["id"]).with_for_update())
    require(bool(row.mfa_pending_secret), "MFA_NOT_CONFIGURED", "Сначала начните настройку", 400)
    secret = cipher(request).decrypt(row.mfa_pending_secret.encode()).decode()
    require(
        pyotp.TOTP(secret).verify(body.code, valid_window=1), "INVALID_OTP", "Неверный код", 403
    )
    row.mfa_secret, row.mfa_pending_secret = row.mfa_pending_secret, None
    codes = [secrets.token_hex(8) for _ in range(8)]
    row.recovery_hashes = [digest(code) for code in codes]
    await db.execute(
        update(Session)
        .where(Session.user_id == row.id, Session.id != user["sid"])
        .values(revoked_at=now())
    )
    await db.execute(update(Session).where(Session.id == user["sid"]).values(mfa_verified=True))
    return {"enabled": True, "recovery_codes": codes}


@app.post("/api/v1/auth/2fa/challenge", response_model=TokenPair)
async def mfa_challenge(
    body: MFAChallenge, request: Request, db=Depends(session, scope="function")
):
    await rate_limit(request, "otp", 20)
    claims = decode_token(body.challenge_token, request.app.state.settings, "mfa_challenge")
    user = await consume_token(db, body.challenge_token, "mfa")
    user = await db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    require(user.id == claims["sub"] and bool(user.mfa_secret), "INVALID_OTP", "Неверный код", 403)
    assert user.mfa_secret is not None
    valid = False
    if body.recovery_code:
        hashed = digest(body.recovery_code)
        valid = hashed in user.recovery_hashes
        if valid:
            user.recovery_hashes = [h for h in user.recovery_hashes if h != hashed]
    elif body.code:
        valid = pyotp.TOTP(cipher(request).decrypt(user.mfa_secret.encode()).decode()).verify(
            body.code, valid_window=1
        )
    require(valid, "INVALID_OTP", "Неверный код", 403)
    return await new_session(
        db, request.app.state.settings, user, request.headers.get("User-Agent", ""), True
    )


@app.post("/api/v1/account/2fa/disable")
async def mfa_disable(
    body: MFADisable,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    row = await db.get(User, user["id"])
    require(
        not (
            request.app.state.settings.admin_2fa_required
            and set(row.roles) & {"admin", "moderator"}
        ),
        "MFA_REQUIRED",
        "Второй фактор обязателен",
        403,
    )
    require(
        await verify_password(row.password_hash, body.current_password),
        "INVALID_PASSWORD",
        "Неверный пароль",
        403,
    )
    require(
        bool(row.mfa_secret)
        and pyotp.TOTP(cipher(request).decrypt(row.mfa_secret.encode()).decode()).verify(
            body.code, valid_window=1
        ),
        "INVALID_OTP",
        "Неверный код",
        403,
    )
    row.mfa_secret, row.recovery_hashes = None, []
    return {"enabled": False}


@app.post("/internal/v1/introspect", dependencies=[Depends(internal())])
async def internal_introspect(
    body: TokenInput, request: Request, db=Depends(session, scope="function")
):
    return await introspect(db, decode_token(body.token, request.app.state.settings))


@app.get("/internal/v1/users/{user_id}", dependencies=[Depends(internal())])
async def internal_user(user_id: str, db=Depends(session, scope="function")):
    user = await db.get(User, user_id)
    require(user is not None, "NOT_FOUND", "Пользователь не найден", 404)
    return {"id": user.id, "status": user.status, "roles": user.roles}


@app.get("/internal/v1/users", dependencies=[Depends(internal("activity"))])
async def internal_users(
    request: Request,
    q: str = "",
    account_status: str | None = None,
    limit: int = 20,
    db=Depends(session, scope="function"),
):
    query = select(User)
    if q:
        query = query.where(User.email.ilike(f"%{q}%"))
    if account_status:
        query = query.where(User.status == account_status)
    result = await paginate(db, query, User, request, limit)
    allowed = {"id", "email", "status", "roles", "email_verified", "created_at", "version"}
    result["items"] = [{k: v for k, v in item.items() if k in allowed} for item in result["items"]]
    return result


class BlockInput(Command):
    action_id: str
    actor_id: str
    blocked: bool
    reason: str = Field(min_length=3, max_length=1000)


@app.post("/internal/v1/users/{user_id}/block", dependencies=[Depends(internal("activity"))])
async def internal_block(user_id: str, body: BlockInput, db=Depends(session, scope="function")):
    previous = await db.scalar(
        select(SecurityAudit).where(SecurityAudit.action_id == body.action_id)
    )
    if previous:
        return {"status": "succeeded", "action_id": body.action_id}
    actor = await db.get(User, body.actor_id)
    target = await db.scalar(select(User).where(User.id == user_id).with_for_update())
    require(
        actor is not None
        and actor.status == "active"
        and set(actor.roles) & {"moderator", "admin"},
        "FORBIDDEN",
        "Нет полномочий",
        403,
    )
    require(target is not None, "NOT_FOUND", "Пользователь не найден", 404)
    require(
        target.id != actor.id
        and ("admin" in actor.roles or not set(target.roles) & {"admin", "moderator"}),
        "FORBIDDEN",
        "Нельзя изменить этот аккаунт",
        403,
    )
    require(
        target.status not in {"deleting", "deleted"}, "INVALID_STATE", "Учётная запись удаляется"
    )
    target.status, target.blocked_reason = ("blocked" if body.blocked else "active"), body.reason
    if body.blocked:
        await db.execute(update(Session).where(Session.user_id == user_id).values(revoked_at=now()))
    db.add(
        SecurityAudit(
            actor_id=actor.id,
            target_id=user_id,
            action="block" if body.blocked else "unblock",
            action_id=body.action_id,
            detail={"reason": body.reason},
        )
    )
    await append_event(
        db, "identity.user_blocked", user_id, {"user_id": user_id, "blocked": body.blocked}
    )
    return {"status": "succeeded", "action_id": body.action_id}


class DeleteAccount(PasswordInput):
    confirm: bool


@app.delete("/api/v1/account/me", status_code=202)
async def delete_account(
    body: DeleteAccount,
    request: Request,
    user=Depends(current_user),
    db=Depends(session, scope="function"),
):
    async def operation():
        row = await db.get(User, user["id"])
        require(
            body.confirm and await verify_password(row.password_hash, body.current_password),
            "CONFIRMATION_REQUIRED",
            "Подтвердите удаление правильным паролем",
            403,
        )
        deletion = AccountDeletion(id=uid(), user_id=row.id)
        db.add(deletion)
        row.status, row.deletion_id = "deleting", deletion.id
        await db.execute(update(Session).where(Session.user_id == row.id).values(revoked_at=now()))
        await append_event(
            db,
            "identity.account_deletion_requested",
            row.id,
            {"user_id": row.id, "deletion_id": deletion.id},
        )
        return {
            "deletion_id": deletion.id,
            "status": "pending",
            "operation_token": token(
                request.app.state.settings,
                row.id,
                "deletion_status",
                86400,
                deletion_id=deletion.id,
            ),
        }

    return await command(request, db, operation, 202)


@app.get("/api/v1/account/deletion/{deletion_id}")
async def deletion_status(
    deletion_id: str,
    request: Request,
    authorization: str = Header(default=""),
    db=Depends(session, scope="function"),
):
    claims = decode_token(
        authorization.removeprefix("Bearer "), request.app.state.settings, "deletion_status"
    )
    row = await db.get(AccountDeletion, deletion_id)
    require(
        row is not None and claims.get("deletion_id") == row.id and claims["sub"] == row.user_id,
        "NOT_FOUND",
        "Операция не найдена",
        404,
    )
    return {"deletion_id": row.id, "status": row.status}
