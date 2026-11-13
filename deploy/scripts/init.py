#!/usr/bin/env python3


import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("identity", "people", "projects", "publications", "media", "activity")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--environment",
        choices=["development", "test", "production"],
        default="development",
    )
    args = parser.parse_args()
    (ROOT / "backups/metrics").mkdir(parents=True, exist_ok=True)
    env_path = (
        ROOT
        / {"development": ".env", "test": ".env.test", "production": ".env.prod"}[args.environment]
    )
    secret_dir = ROOT / "deploy" / ".secrets" / args.environment
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if env_path.exists():
        print(f"Configuration already exists: {env_path.name}; preserving it")
        return
    private_path = secret_dir / "jwt_private.pem"
    public_path = secret_dir / "jwt_public.pem"
    if any(key.exists() or key.is_symlink() for key in (private_path, public_path)):
        raise SystemExit(
            "Signing keys already exist without matching configuration; restore the original .env instead of overwriting keys"
        )
    private = Ed25519PrivateKey.generate()
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
    runtime_uid = os.getuid() or 10001
    runtime_gid = os.getgid() or 10001
    os.chmod(secret_dir, 0o755)
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)
    if os.getuid() == 0:
        os.chown(private_path, runtime_uid, runtime_gid)
    values = {
        "COMPOSE_PROJECT_NAME": {
            "development": "science",
            "test": "science-test",
            "production": "science-prod",
        }[args.environment],
        "SC_ENVIRONMENT": args.environment,
        "APP_UID": str(runtime_uid),
        "APP_GID": str(runtime_gid),
        "APP_PORT": "18081" if args.environment == "test" else "18080",
        "S3_PORT": "19001" if args.environment == "test" else "19000",
        "MAIL_PORT": "18026" if args.environment == "test" else "18025",
        "SECRET_DIRECTORY": f"./.secrets/{args.environment}",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(30),
        "RABBITMQ_PASSWORD": secrets.token_urlsafe(30),
        "RABBITMQ_HOSTNAME": "rabbitmq",
        "RABBITMQ_NODENAME": "rabbit@rabbitmq",
        "REDIS_PASSWORD": secrets.token_urlsafe(30),
        "MINIO_ROOT_USER": "science-root",
        "MINIO_ROOT_PASSWORD": secrets.token_urlsafe(30),
        "S3_ACCESS_KEY_ID": "science-app",
        "S3_SECRET_ACCESS_KEY": secrets.token_urlsafe(30),
        "GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "MFA_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "BACKUP_PASSPHRASE": secrets.token_urlsafe(40),
    }
    tokens = {name: secrets.token_urlsafe(40) for name in SERVICES}
    values["INTERNAL_TOKEN_HASHES"] = json.dumps(
        {name: hashlib.sha256(value.encode()).hexdigest() for name, value in tokens.items()},
        separators=(",", ":"),
    )
    for name in SERVICES:
        values[f"{name.upper()}_DATABASE_PASSWORD"] = secrets.token_urlsafe(30)
        values[f"{name.upper()}_SERVICE_TOKEN"] = tokens[name]
    if args.environment == "production":
        values.update(
            {
                "ADMIN_2FA_REQUIRED": "true",
                "SCANNER_REQUIRED": "true",
                "PUBLIC_URL": "https://science.example.org",
                "S3_PUBLIC_ENDPOINT": "https://science.example.org:9443",
                "ALLOWED_ORIGINS": '["https://science.example.org"]',
                "TLS_APP_PORT": "443",
                "TLS_S3_PORT": "9443",
                "SMTP_HOST": "",
                "SMTP_PORT": "587",
                "SMTP_SECURITY": "starttls",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "MAIL_FROM": "",
            }
        )
    env_path.write_text("\n".join(f"{key}='{value}'" for key, value in values.items()) + "\n")
    os.chmod(env_path, 0o600)
    print(f"Created {env_path.name} and signing keys; secret values were not printed")


if __name__ == "__main__":
    main()
