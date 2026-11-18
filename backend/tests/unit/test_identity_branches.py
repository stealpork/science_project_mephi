from datetime import timedelta
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pyotp
import pytest
from cryptography.fernet import Fernet

from libs.platform.database import now, uid
from libs.platform.errors import APIError
from services.identity import auth
from services.identity.models import Session
from tests.unit.support import database, operation, request, rows, service

pytestmark = pytest.mark.unit


@pytest.fixture
def identity(monkeypatch):
    module = service(monkeypatch, "identity")
    monkeypatch.setattr(module, "rate_limit", AsyncMock())
    monkeypatch.setattr(module, "command", operation)
    monkeypatch.setattr(module, "append_event", AsyncMock())
    return module


def account(**changes):
    return Obj(
        **{
            "id": uid(),
            "email": "member@example.org",
            "password_hash": "old hash",
            "email_verified": False,
            "status": "active",
            "roles": ["user"],
            "mfa_secret": None,
            "mfa_pending_secret": None,
            "recovery_hashes": [],
            **changes,
        }
    )


@pytest.mark.parametrize(
    "state",
    [
        "missing-user",
        "missing-session",
        "wrong-user",
        "revoked",
        "expired",
        "blocked",
        "deleting",
        "valid",
        "no-sid",
    ],
)
async def test_introspection_requires_live_session_and_active_account(state):
    user = account(status=state if state in {"blocked", "deleting"} else "active")
    session = Obj(
        id="sid",
        user_id="other" if state == "wrong-user" else user.id,
        revoked_at=now() if state == "revoked" else None,
        expires_at=now() + timedelta(seconds=-1 if state == "expired" else 60),
        mfa_verified=True,
    )
    db = database()
    db.get.side_effect = [
        None if state == "missing-user" else user,
        None if state == "missing-session" else session,
    ]
    claims = {"sub": user.id, **({} if state == "no-sid" else {"sid": "sid"})}
    if state == "valid":
        result = await auth.introspect(db, claims)
        assert result == {
            "id": user.id,
            "roles": ["user"],
            "email_verified": False,
            "sid": "sid",
            "mfa_verified": True,
        }
    else:
        with pytest.raises(APIError) as error:
            await auth.introspect(db, claims)
        assert error.value.code == (
            "ACCOUNT_BLOCKED"
            if state == "blocked"
            else "ACCOUNT_UNAVAILABLE"
            if state == "deleting"
            else "SESSION_REVOKED"
        )


@pytest.mark.parametrize(
    "exists,valid,status",
    [
        (False, False, "active"),
        (True, False, "active"),
        (True, True, "blocked"),
        (True, True, "active"),
    ],
)
async def test_login_policy_checks_dummy_hash_and_account_status(
    monkeypatch, exists, valid, status
):
    db, user = database(), account(status=status)
    db.scalar.return_value = user if exists else None
    verify = AsyncMock(return_value=valid)
    monkeypatch.setattr(auth, "verify_password", verify)
    if exists and valid and status == "active":
        assert await auth.check_user(db, "MEMBER@example.org", "password") is user
    else:
        with pytest.raises(APIError) as error:
            await auth.check_user(db, "MEMBER@example.org", "password")
        assert error.value.status == (403 if valid and exists else 401)
    assert verify.call_args.args[0] == (user.password_hash if exists else auth.dummy_password_hash)


@pytest.mark.parametrize("state", ["missing", "consumed", "expired", "no-user", "blocked", "valid"])
async def test_one_time_action_tokens(identity, state):
    db = database()
    row = Obj(
        user_id="u",
        consumed_at=now() if state == "consumed" else None,
        expires_at=now() + timedelta(seconds=-1 if state == "expired" else 60),
    )
    db.scalar.return_value = None if state == "missing" else row
    user = account(status="blocked" if state == "blocked" else "active")
    db.get.return_value = None if state == "no-user" else user
    if state == "valid":
        assert await identity.consume_token(db, "secret", "mfa") is user
        assert row.consumed_at is not None
    else:
        with pytest.raises(APIError) as error:
            await identity.consume_token(db, "secret", "mfa")
        assert error.value.status == (403 if state in {"no-user", "blocked"} else 400)


@pytest.mark.parametrize(
    "mfa,required,roles",
    [
        (True, False, ["user"]),
        (False, True, ["admin"]),
        (False, True, ["user"]),
        (False, False, ["user"]),
    ],
)
async def test_login_selects_challenge_setup_or_session(
    identity, monkeypatch, mfa, required, roles
):
    user = account(mfa_secret="encrypted" if mfa else None, roles=roles)
    monkeypatch.setattr(identity, "check_user", AsyncMock(return_value=user))
    monkeypatch.setattr(identity, "token", lambda *args, **kwargs: "challenge")
    new = AsyncMock(return_value={"pair": True})
    monkeypatch.setattr(identity, "new_session", new)
    req, db = request(), database()
    req.app.state.settings.admin_2fa_required = required
    body = identity.Login(email=user.email, password="password")
    if required and "admin" in roles and not mfa:
        with pytest.raises(APIError, match="Сначала"):
            await identity.login(body, req, db)
        new.assert_not_awaited()
    else:
        result = await identity.login(body, req, db)
        if mfa:
            assert result["requires_2fa"] and db.add.call_args.args[0].token_hash == auth.digest(
                "challenge"
            )
            new.assert_not_awaited()
        else:
            assert result == {"pair": True}


@pytest.mark.parametrize(
    "handler,exists,verified",
    [
        ("resend", False, False),
        ("resend", True, False),
        ("resend", True, True),
        ("forgot", False, False),
        ("forgot", True, False),
    ],
)
async def test_email_endpoints_do_not_reveal_presence(
    identity, monkeypatch, handler, exists, verified
):
    db, req = database(), request()
    db.scalar.return_value = account(email_verified=verified) if exists else None
    send = AsyncMock()
    monkeypatch.setattr(identity, "send_token", send)
    assert await getattr(identity, handler)(
        identity.EmailInput(email="member@example.org"), req, db
    ) == {"status": "accepted"}
    assert send.await_count == int(exists and (handler == "forgot" or not verified))


async def test_sessions_revoke_and_password_mutations(identity, monkeypatch):
    db, user = database(), account()
    db.get.return_value = user
    monkeypatch.setattr(identity, "consume_token", AsyncMock(return_value=user))
    monkeypatch.setattr(identity, "hash_password", AsyncMock(return_value="new hash"))
    monkeypatch.setattr(identity, "verify_password", AsyncMock(return_value=True))
    assert await identity.verify_email(identity.TokenInput(token="valid"), db) == {"verified": True}
    assert user.email_verified
    assert await identity.reset_password(
        identity.PasswordReset(token="valid", new_password="new password"), db
    ) == {"status": "changed"}
    assert user.password_hash == "new hash" and db.execute.await_count == 2
    assert await identity.change_password(
        identity.PasswordChange(current_password="old password", new_password="new password"),
        {"id": user.id, "sid": "s"},
        db,
    ) == {"status": "changed"}
    assert db.execute.await_count == 3
    session = Session(
        id=uid(),
        user_id=user.id,
        refresh_hash="private",
        expires_at=now() + timedelta(days=1),
        user_agent="browser",
        mfa_verified=False,
    )
    db.scalars.return_value = rows([session])
    result = await identity.sessions({"id": user.id}, db)
    assert "refresh_hash" not in result["items"][0]
    db.get.return_value = session
    await identity.revoke(session.id, {"id": user.id}, db)
    assert session.revoked_at
    session.revoked_at = None
    await identity.logout({"sid": session.id}, db)
    assert session.revoked_at


@pytest.mark.parametrize("valid_password", [False, True])
async def test_mfa_setup_and_enable(identity, monkeypatch, valid_password):
    req, db, user = request(), database(), account()
    req.app.state.settings.mfa_encryption_key = Fernet.generate_key().decode()
    db.get.return_value = db.scalar.return_value = user
    monkeypatch.setattr(identity, "verify_password", AsyncMock(return_value=valid_password))
    if not valid_password:
        with pytest.raises(APIError) as error:
            await identity.mfa_setup(
                identity.PasswordInput(current_password="bad"), req, {"id": user.id}, db
            )
        assert error.value.code == "INVALID_PASSWORD" and user.mfa_pending_secret is None
        return
    result = await identity.mfa_setup(
        identity.PasswordInput(current_password="good"), req, {"id": user.id}, db
    )
    assert result["provisioning_uri"].startswith("otpauth://")
    secret = identity.cipher(req).decrypt(user.mfa_pending_secret.encode()).decode()
    enabled = await identity.mfa_enable(
        identity.CodeInput(code=pyotp.TOTP(secret).now()), req, {"id": user.id, "sid": "s"}, db
    )
    assert enabled["enabled"] and len(enabled["recovery_codes"]) == 8
    assert user.mfa_secret and user.mfa_pending_secret is None
    assert set(user.recovery_hashes) == {auth.digest(code) for code in enabled["recovery_codes"]}
    assert db.execute.await_count == 2


@pytest.mark.parametrize(
    "kind", ["recovery", "bad-recovery", "totp", "bad-totp", "no-code", "wrong-sub", "disabled"]
)
async def test_mfa_challenge_consumes_recovery_exactly_once(identity, monkeypatch, kind):
    req, db, user = request(), database(), account()
    req.app.state.settings.mfa_encryption_key = Fernet.generate_key().decode()
    secret = pyotp.random_base32()
    user.mfa_secret = (
        identity.cipher(req).encrypt(secret.encode()).decode() if kind != "disabled" else None
    )
    user.recovery_hashes = [auth.digest("recovery"), auth.digest("second")]
    monkeypatch.setattr(identity, "consume_token", AsyncMock(return_value=user))
    monkeypatch.setattr(
        identity, "decode_token", lambda *args: {"sub": "wrong" if kind == "wrong-sub" else user.id}
    )
    new = AsyncMock(return_value={"pair": True})
    monkeypatch.setattr(identity, "new_session", new)
    db.scalar.return_value = user
    body = identity.MFAChallenge(
        challenge_token="challenge",
        recovery_code=("recovery" if kind == "recovery" else "bad") if "recovery" in kind else None,
        code=pyotp.TOTP(secret).now()
        if kind == "totp"
        else "invalid"
        if kind == "bad-totp"
        else None,
    )
    if kind in {"recovery", "totp"}:
        assert await identity.mfa_challenge(body, req, db) == {"pair": True}
        assert new.call_args.args[-1] is True
        assert len(user.recovery_hashes) == (1 if kind == "recovery" else 2)
    else:
        with pytest.raises(APIError) as error:
            await identity.mfa_challenge(body, req, db)
        assert error.value.code == "INVALID_OTP"
        new.assert_not_awaited()


@pytest.mark.parametrize("kind", ["valid", "required", "bad-password", "no-secret", "bad-code"])
async def test_mfa_disable_policy(identity, monkeypatch, kind):
    req, db, user = request(), database(), account(roles=["admin"])
    req.app.state.settings.mfa_encryption_key = Fernet.generate_key().decode()
    req.app.state.settings.admin_2fa_required = kind == "required"
    secret = pyotp.random_base32()
    user.mfa_secret = (
        None if kind == "no-secret" else identity.cipher(req).encrypt(secret.encode()).decode()
    )
    db.get.return_value = user
    monkeypatch.setattr(identity, "verify_password", AsyncMock(return_value=kind != "bad-password"))
    body = identity.MFADisable(
        current_password="password",
        code="invalid" if kind == "bad-code" else pyotp.TOTP(secret).now(),
    )
    if kind == "valid":
        assert await identity.mfa_disable(body, req, {"id": user.id}, db) == {"enabled": False}
        assert user.mfa_secret is None and user.recovery_hashes == []
    else:
        with pytest.raises(APIError) as error:
            await identity.mfa_disable(body, req, {"id": user.id}, db)
        assert error.value.code == {
            "required": "MFA_REQUIRED",
            "bad-password": "INVALID_PASSWORD",
        }.get(kind, "INVALID_OTP")


@pytest.mark.parametrize("prior,blocked", [(True, True), (False, True), (False, False)])
async def test_internal_block_is_idempotent_and_revokes_only_on_block(identity, prior, blocked):
    db, actor, target = database(), account(roles=["admin"]), account()
    db.scalar.side_effect = [Obj(id="audit") if prior else None, target]
    db.get.return_value = actor
    body = identity.BlockInput(
        action_id=uid(), actor_id=actor.id, blocked=blocked, reason="policy violation"
    )
    result = await identity.internal_block(target.id, body, db)
    assert result["status"] == "succeeded"
    if prior:
        db.add.assert_not_called()
        identity.append_event.assert_not_awaited()
    else:
        assert target.status == ("blocked" if blocked else "active")
        assert db.execute.await_count == int(blocked)
        assert db.add.call_args.args[0].action == ("block" if blocked else "unblock")


async def test_internal_user_catalog_filters_and_redacts(identity, monkeypatch):
    db = database()
    pagination = AsyncMock(
        return_value={
            "items": [{"id": "u", "email": "e", "password_hash": "secret", "mfa_secret": "secret"}],
            "has_more": False,
            "next_cursor": None,
        }
    )
    monkeypatch.setattr(identity, "paginate", pagination)
    result = await identity.internal_users(
        request(), q="member", account_status="blocked", limit=10, db=db
    )
    assert result["items"] == [{"id": "u", "email": "e"}]
    sql = str(pagination.call_args.args[1])
    assert "users.email" in sql and "users.status" in sql


async def test_delete_and_introspection_authorization_branches(identity, monkeypatch):
    req, db = request(), database()
    row = Obj(id=uid(), user_id="u", status="completed")
    db.get.return_value = row
    monkeypatch.setattr(identity, "decode_token", lambda *args: {"sub": "u", "deletion_id": row.id})
    assert (await identity.deletion_status(row.id, req, "Bearer token", db))[
        "status"
    ] == "completed"
    monkeypatch.setattr(identity, "introspect", AsyncMock(return_value={"id": "u"}))
    assert await identity.internal_introspect(identity.TokenInput(token="valid"), req, db) == {
        "id": "u"
    }


@pytest.mark.parametrize("faculty", [False, True])
async def test_registration_validates_reference_hierarchy_and_keeps_password_out_of_events(
    identity, monkeypatch, faculty
):
    db = database()
    university_id, faculty_id, specialty_id = uid(), uid(), uid()
    references = [
        {"id": university_id, "kind": "universities"},
        {"id": specialty_id, "kind": "specialties"},
    ] + ([{"id": faculty_id, "kind": "faculties", "parent_id": university_id}] if faculty else [])
    monkeypatch.setattr(identity, "internal_call", AsyncMock(return_value={"items": references}))
    monkeypatch.setattr(identity, "hash_password", AsyncMock(return_value="hashed"))
    monkeypatch.setattr(identity, "send_token", AsyncMock())
    body = identity.Register(
        first_name="Имя",
        last_name="Фамилия",
        email="member@example.org",
        password="Secret password",
        university_id=university_id,
        faculty_id=faculty_id if faculty else None,
        specialty_id=specialty_id,
    )
    result = await identity.register(body, request(), db)
    assert (
        result["profile_status"] == "initializing"
        and db.add.call_args.args[0].password_hash == "hashed"
    )
    assert "password" not in identity.append_event.call_args.args[-1]


@pytest.mark.parametrize("reused", [False, True])
async def test_refresh_rotation_and_reuse_revocation_survive_error(identity, monkeypatch, reused):
    db, user = database(), account()
    session = Obj(
        user_id=user.id,
        revoked_at=None,
        expires_at=now() + timedelta(days=1),
        refresh_hash="old" if reused else auth.digest("refresh"),
    )
    db.scalar.return_value, db.get.return_value = session, user
    monkeypatch.setattr(identity, "decode_token", lambda *args: {"sub": user.id, "sid": "s"})
    pair = {"access_token": "rotated"}
    monkeypatch.setattr(identity, "issue_pair", lambda *args: pair)
    if reused:
        with pytest.raises(APIError) as error:
            await identity.refresh(identity.Refresh(refresh_token="refresh"), request(), db)
        assert error.value.code == "REFRESH_REUSED" and session.revoked_at
        db.commit.assert_awaited_once()
    else:
        assert (
            await identity.refresh(identity.Refresh(refresh_token="refresh"), request(), db) == pair
        )
        db.commit.assert_not_awaited()


async def test_internal_user_returns_only_safe_fields(identity):
    db, user = database(), account()
    db.get.return_value = user
    assert await identity.internal_user(user.id, db) == {
        "id": user.id,
        "status": "active",
        "roles": ["user"],
    }


@pytest.mark.parametrize("confirm", [False, True])
async def test_delete_requires_both_confirmation_and_correct_password(
    identity, monkeypatch, confirm
):
    db, user = database(), account()
    db.get.return_value = user
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(identity, "verify_password", verify)
    with pytest.raises(APIError) as error:
        await identity.delete_account(
            identity.DeleteAccount(current_password="wrong", confirm=confirm),
            request(),
            {"id": user.id},
            db,
        )
    assert error.value.code == "CONFIRMATION_REQUIRED" and verify.await_count == int(confirm)
    db.add.assert_not_called()


async def test_confirmed_deletion_revokes_sessions_and_returns_scoped_operation_token(
    identity, monkeypatch
):
    db, user = database(), account()
    db.get.return_value = user
    monkeypatch.setattr(identity, "verify_password", AsyncMock(return_value=True))
    token = MagicMock(return_value="scoped-operation-token")
    monkeypatch.setattr(identity, "token", token)
    result = await identity.delete_account(
        identity.DeleteAccount(current_password="correct", confirm=True),
        request(),
        {"id": user.id},
        db,
    )
    assert result["status"] == "pending" and result["operation_token"] == "scoped-operation-token"
    assert user.status == "deleting" and result["deletion_id"] == user.deletion_id
    assert token.call_args.args[1:4] == (user.id, "deletion_status", 86400)
    assert token.call_args.kwargs == {"deletion_id": user.deletion_id}
    db.execute.assert_awaited_once()
