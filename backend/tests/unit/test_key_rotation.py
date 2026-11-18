import json
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from libs.platform.config import Settings
from libs.platform.errors import APIError
from libs.platform.http import decode_token
from services.identity.auth import token
from tests.unit.test_security import signing as signing

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("kind", ["access", "refresh"])
def test_old_and_new_keys_overlap_then_retired_key_is_rejected(signing, tmp_path, kind):
    old_public = signing.jwt_public_key_file.read_text()
    old_token = token(signing, str(uuid4()), kind, 60, sid=str(uuid4()))
    private = Ed25519PrivateKey.generate()
    private_path, public_path = tmp_path / "new-private.pem", tmp_path / "new-public.pem"
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
    rotated = signing.model_copy(
        update={
            "jwt_key_id": "rotated-v2",
            "jwt_private_key_file": private_path,
            "jwt_public_key_file": public_path,
            "jwt_additional_public_keys": {signing.jwt_key_id: old_public},
        }
    )
    new_token = token(rotated, str(uuid4()), kind, 60, sid=str(uuid4()))
    assert decode_token(old_token, rotated, kind)
    assert decode_token(new_token, rotated, kind)
    retired = rotated.model_copy(update={"jwt_additional_public_keys": {}})
    assert decode_token(new_token, retired, kind)
    with pytest.raises(APIError) as error:
        decode_token(old_token, retired, kind)
    assert error.value.status == 401


@pytest.mark.parametrize("identifier", [None, {}, [], "not-trusted"])
def test_untrusted_key_identifier_is_a_401_not_a_500(signing, identifier):
    header = jwt.utils.base64url_encode(
        json.dumps({"alg": "none", "kid": identifier}).encode()
    ).decode()
    payload = jwt.utils.base64url_encode(json.dumps({"sub": str(uuid4())}).encode()).decode()
    encoded = header + "." + payload + "."
    with pytest.raises(APIError) as error:
        decode_token(encoded, signing)
    assert error.value.status == 401


@pytest.mark.parametrize(
    "values",
    [
        {"jwt_key_id": ""},
        {"jwt_additional_public_keys": {"local-v1": "not-a-key"}},
        {"jwt_additional_public_keys": {"old": "not-a-key"}},
        {"access_ttl_seconds": -1},
        {"refresh_ttl_seconds": 0},
        {"max_pdf_bytes": 25_000_001},
        {"max_avatar_bytes": 5_000_001},
    ],
)
def test_invalid_security_settings_never_boot(values):
    with pytest.raises(ValidationError):
        Settings(**values)
