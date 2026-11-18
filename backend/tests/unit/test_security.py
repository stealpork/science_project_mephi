from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from libs.platform.api import Command, assert_version
from libs.platform.config import Settings
from libs.platform.database import now
from libs.platform.errors import APIError
from libs.platform.http import admin, decode_token, moderator
from services.identity.auth import hash_password, token, verify_password

pytestmark = pytest.mark.unit


@pytest.fixture
def signing(tmp_path):
    private = Ed25519PrivateKey.generate()
    private_path: Path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return Settings(jwt_private_key_file=private_path, jwt_public_key_file=public_path)


def test_jwt_correct_signature_and_audience(signing):
    identifier = str(uuid4())
    value = token(signing, identifier, "access", 60, sid=str(uuid4()))
    assert decode_token(value, signing)["sub"] == identifier


@pytest.mark.parametrize("kind", ["refresh", "mfa_challenge", "deletion_status"])
def test_other_token_types_are_not_access_tokens(signing, kind):
    value = token(signing, str(uuid4()), kind, 60)
    with pytest.raises(APIError) as error:
        decode_token(value, signing)
    assert error.value.status == 401


def test_expired_jwt(signing):
    value = token(signing, str(uuid4()), "access", -60, sid=str(uuid4()))
    with pytest.raises(APIError) as error:
        decode_token(value, signing)
    assert error.value.code == "TOKEN_EXPIRED"


@pytest.mark.parametrize("kind", ["access", "refresh"])
def test_session_tokens_without_session_identifier_are_rejected(signing, kind):
    value = token(signing, str(uuid4()), kind, 60)
    with pytest.raises(APIError) as error:
        decode_token(value, signing, kind)
    assert error.value.code == "INVALID_TOKEN"


def test_signed_token_type_cannot_disagree_with_access_audience(signing):
    value = token(signing, str(uuid4()), "access", 60, sid=str(uuid4()), token_type="refresh")
    with pytest.raises(APIError) as error:
        decode_token(value, signing)
    assert error.value.code == "INVALID_TOKEN"


def test_unsigned_and_hmac_tokens_rejected(signing):
    for algorithm in ["none", "HS256"]:
        value = jwt.encode(
            {"sub": str(uuid4()), "exp": now() + timedelta(minutes=1)},
            key="" if algorithm == "none" else "x" * 32,
            algorithm=algorithm,
        )
        with pytest.raises(APIError):
            decode_token(value, signing)


async def test_argon2_password_hash_is_salted_and_validated():
    first = await hash_password("secure-password")
    second = await hash_password("secure-password")
    assert first != second and first.startswith("$argon2id$")
    assert await verify_password(first, "secure-password")
    assert not await verify_password(first, "wrong")
    assert not await verify_password("!deleted", "secure-password")


@pytest.mark.parametrize("roles", [["user"], [], ["editor"], ["leader"]])
def test_domain_roles_are_not_global_moderators(roles):
    with pytest.raises(APIError):
        moderator({"roles": roles})


def test_moderator_not_admin():
    user = {"roles": ["moderator"]}
    assert moderator(user) is user
    with pytest.raises(APIError):
        admin(user)
    assert admin({"roles": ["admin"]})["roles"] == ["admin"]


def test_version_conflict_does_not_increment():
    row = SimpleNamespace(version=3, updated_at=now())
    with pytest.raises(APIError) as error:
        assert_version(row, 2)
    assert error.value.code == "VERSION_CONFLICT" and row.version == 3
    assert_version(row, 3)
    assert row.version == 4


def test_command_forbids_extra_fields_and_malformed_ids():
    class Example(Command):
        user_id: str

    with pytest.raises(ValidationError):
        Example(user_id="not-an-id")
    with pytest.raises(ValidationError):
        Example(user_id=str(uuid4()), injected_role="admin")
