from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICES = ("identity", "people", "projects", "publications", "media", "activity")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SC_", extra="ignore", hide_input_in_errors=True)

    service_name: str = "identity"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://identity:development@postgres/identity"
    rabbitmq_url: str = "amqp://science:development@rabbitmq/"
    redis_url: str = "redis://redis:6379/0"
    service_token: str = ""
    internal_token_hashes: dict[str, str] = {}
    jwt_private_key_file: Path = Path("/run/secrets/jwt_private.pem")
    jwt_public_key_file: Path = Path("/run/secrets/jwt_public.pem")
    jwt_issuer: str = "science-interaction"
    jwt_key_id: str = "local-v1"
    jwt_additional_public_keys: dict[str, str] | None = None
    access_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    refresh_ttl_seconds: int = Field(default=2_592_000, ge=300, le=7_776_000)
    allowed_origins: list[str] = ["http://localhost:18080", "http://localhost:4173"]
    public_url: str = "http://localhost:18080"
    email_verification_required: bool = False
    admin_2fa_required: bool = False
    mfa_encryption_key: str = ""
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_security: str = "none"
    mail_from: str = "science@example.test"
    s3_internal_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str = "http://localhost:19000"
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_addressing_style: str = "path"
    s3_manage_cors: bool = False
    s3_quarantine_bucket: str = "science-quarantine"
    s3_files_bucket: str = "science-files"
    max_pdf_bytes: int = Field(default=25_000_000, ge=1, le=25_000_000)
    max_avatar_bytes: int = Field(default=5_000_000, ge=1, le=5_000_000)
    scanner_host: str = "clamav"
    scanner_port: int = 3310
    scanner_required: bool = True
    http_timeout_seconds: float = 8
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 15000
    db_lock_timeout_ms: int = 5000
    trusted_proxy_hosts: list[str] = ["nginx"]

    @model_validator(mode="after")
    def production_guards(self):
        if self.service_name not in SERVICES:
            raise ValueError("Unknown service")
        keys = self.jwt_additional_public_keys or {}
        if (
            not self.jwt_key_id
            or len(self.jwt_key_id) > 100
            or len(keys) > 5
            or self.jwt_key_id in keys
        ):
            raise ValueError(
                "Use a unique active JWT key ID and at most five additional public keys"
            )
        for identifier, value in keys.items():
            if not identifier or len(identifier) > 100 or len(value) > 1024:
                raise ValueError("Invalid JWT verification key")
            try:
                public = serialization.load_pem_public_key(value.encode())
                if not isinstance(public, Ed25519PublicKey):
                    raise ValueError("Wrong key algorithm")
            except (ValueError, TypeError) as exc:
                raise ValueError("JWT verification keys must be Ed25519 public PEM keys") from exc
        if self.environment == "production":
            if not self.scanner_required or not self.admin_2fa_required:
                raise ValueError("Production requires scanner and administrator MFA")
            if not self.public_url.startswith("https://") or not self.s3_public_endpoint.startswith(
                "https://"
            ):
                raise ValueError("Production public endpoints require HTTPS")
            if any(not origin.startswith("https://") for origin in self.allowed_origins):
                raise ValueError("Production CORS requires explicit HTTPS origins")
            if len(self.service_token) < 32 or not self.internal_token_hashes:
                raise ValueError("Production requires service credentials")
            if self.service_name == "identity":
                try:
                    Fernet(self.mfa_encryption_key.encode())
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        "Production identity requires a valid MFA encryption key"
                    ) from exc
            if self.service_name == "identity" and self.smtp_security not in {"tls", "starttls"}:
                raise ValueError("Production SMTP requires TLS")
        return self

    def service_url(self, service: str) -> str:
        if service not in SERVICES:
            raise ValueError("Unknown service")
        return f"http://{service}:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
