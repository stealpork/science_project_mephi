import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from libs.platform.config import Settings

pytestmark = pytest.mark.unit


def production(**overrides):
    values = {
        "environment": "production",
        "service_name": "identity",
        "public_url": "https://science.example.org",
        "s3_public_endpoint": "https://files.example.org",
        "allowed_origins": ["https://science.example.org"],
        "admin_2fa_required": True,
        "scanner_required": True,
        "service_token": "sensitive-value-never-log-123456789",
        "internal_token_hashes": {"identity": "some-hash"},
        "smtp_security": "tls",
        "mfa_encryption_key": Fernet.generate_key().decode(),
    }
    return Settings(**{**values, **overrides})


def test_valid_production_baseline():
    assert production().environment == "production"


@pytest.mark.parametrize(
    "override",
    [
        {"public_url": "http://localhost"},
        {"s3_public_endpoint": "http://localhost"},
        {"admin_2fa_required": False},
        {"scanner_required": False},
        {"service_token": "short"},
        {"internal_token_hashes": {}},
        {"smtp_security": "none"},
        {"allowed_origins": ["*"]},
        {"mfa_encryption_key": ""},
    ],
)
def test_production_rejects_insecure_configuration_without_logging_secrets(override):
    with pytest.raises(ValidationError) as error:
        production(**override)
    assert "sensitive-value-never-log" not in str(error.value)


def test_unknown_service_cannot_boot():
    with pytest.raises(ValidationError):
        Settings(service_name="not-a-service")
