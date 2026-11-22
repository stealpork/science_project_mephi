import argparse
import fcntl
import hashlib
import json
import os
import re
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from ops import APPS, ROOT, SERVICES, Ops, backup, call
from tls import configure, directory

from libs.platform.config import Settings

DIGEST = re.compile(r"[a-z0-9][a-z0-9./:_-]*@sha256:[a-f0-9]{64}")
INFRASTRUCTURE = {
    "postgres": "postgres:17-alpine",
    "redis": "redis:7.4-alpine",
    "rabbitmq": "rabbitmq:4.2-management-alpine",
    "clamav": "clamav/clamav:1.4",
}


def image(value):
    if not value or "//" in value or not DIGEST.fullmatch(value):
        raise SystemExit(
            "Supply a registry image@sha256:<64 lowercase hex digits>; mutable tags are forbidden"
        )
    return value


def validate(ops):
    c = ops.config
    if c.get("SC_ENVIRONMENT") != "production":
        raise SystemExit("Production requires SC_ENVIRONMENT=production")
    if ops.envfile.stat().st_mode & 0o077:
        raise SystemExit("Production environment file must be private: chmod 600")
    if c.get("SMTP_HOST") in {None, "", "mailpit", "localhost", "127.0.0.1"}:
        raise SystemExit("Configure a real SMTP_HOST instead of Mailpit")
    if not c.get("MAIL_FROM") or c["MAIL_FROM"].endswith((".test", "@example.org")):
        raise SystemExit("Configure the real MAIL_FROM address")
    for name in ["PUBLIC_URL", "S3_PUBLIC_ENDPOINT"]:
        url = urlsplit(c.get(name) or "")
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise SystemExit(
                f"{name} must be an HTTPS origin without credentials, path, query or fragment"
            )
        expected_port = int(
            c.get("TLS_APP_PORT" if name == "PUBLIC_URL" else "TLS_S3_PORT")
            or (443 if name == "PUBLIC_URL" else 9443)
        )
        if (url.port or 443) != expected_port:
            raise SystemExit(f"{name} port does not match the configured TLS listener")
    if c.get("TLS_APP_PORT", "443") == c.get("TLS_S3_PORT", "9443"):
        raise SystemExit("Application and S3 TLS listeners require different host ports")
    origins = json.loads(c.get("ALLOWED_ORIGINS") or "[]")
    if origins != [c["PUBLIC_URL"].rstrip("/")]:
        raise SystemExit("ALLOWED_ORIGINS must contain only the production PUBLIC_URL origin")
    hashes = json.loads(c.get("INTERNAL_TOKEN_HASHES") or "{}")
    for name in SERVICES:
        token = c.get(f"{name.upper()}_SERVICE_TOKEN") or ""
        if hashlib.sha256(token.encode()).hexdigest() != hashes.get(name):
            raise SystemExit(f"Service token hash mismatch: {name}")
        Settings(
            service_name=name,
            environment="production",
            service_token=token,
            internal_token_hashes=hashes,
            public_url=c["PUBLIC_URL"],
            s3_public_endpoint=c["S3_PUBLIC_ENDPOINT"],
            allowed_origins=origins,
            scanner_required=c.get("SCANNER_REQUIRED") == "true",
            admin_2fa_required=c.get("ADMIN_2FA_REQUIRED") == "true",
            smtp_security=c.get("SMTP_SECURITY") or "none",
            mfa_encryption_key=c.get("MFA_ENCRYPTION_KEY") or "",
        )
    for name in [
        "POSTGRES_PASSWORD",
        "RABBITMQ_PASSWORD",
        "REDIS_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "S3_SECRET_ACCESS_KEY",
        "BACKUP_PASSPHRASE",
        *(f"{s.upper()}_DATABASE_PASSWORD" for s in SERVICES),
    ]:
        if len(c.get(name) or "") < 24:
            raise SystemExit(f"A unique strong secret is required: {name}")
    if any(
        c.get(key)
        for key in [
            "S3_INTERNAL_ENDPOINT",
            "RABBITMQ_URL",
            "REDIS_URL",
            *(f"{s.upper()}_DATABASE_URL" for s in SERVICES),
        ]
    ):
        raise SystemExit(
            "This single-host backup/deployment workflow requires its local infrastructure; external providers need separate backup orchestration"
        )
    secrets = directory(ops).parent
    for name in ["jwt_private.pem", "jwt_public.pem"]:
        if not (secrets / name).is_file():
            raise SystemExit(f"Missing signing key: {name}")
    private = secrets / "jwt_private.pem"
    if private.stat().st_mode & 0o077:
        raise SystemExit("JWT private key must have mode 600")
    signing = serialization.load_pem_private_key(private.read_bytes(), password=None)
    public = serialization.load_pem_public_key((secrets / "jwt_public.pem").read_bytes())
    if signing.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ) != public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw):
        raise SystemExit("JWT signing keys do not match")
    server = directory(ops) / "server"
    if (server / "server.key").stat().st_mode & 0o077:
        raise SystemExit("TLS private key must have mode 600")
    certificate = x509.load_pem_x509_certificate((server / "server.crt").read_bytes())
    tls_key = serialization.load_pem_private_key(
        (server / "server.key").read_bytes(), password=None
    )
    if certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != tls_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise SystemExit("TLS certificate and private key do not match")
    if not certificate.not_valid_before_utc <= datetime.now(UTC) < certificate.not_valid_after_utc:
        raise SystemExit("TLS certificate is expired or not active yet")
    if certificate.issuer == certificate.subject or (directory(ops) / "ca.key").exists():
        raise SystemExit("A trusted production TLS certificate is required, not a development CA")
    names = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName)
    for key in ["PUBLIC_URL", "S3_PUBLIC_ENDPOINT"]:
        hostname = urlsplit(c[key]).hostname
        if not any(
            hostname == name
            or (
                name.startswith("*.")
                and hostname.count(".") == name.count(".")
                and hostname.endswith(name[1:])
            )
            for name in names
        ):
            raise SystemExit(f"TLS certificate does not cover {key}")
    return secrets


def saved(ops):
    return ROOT / "deploy/releases" / ops.project / "current.json"


def overlay(ops, images, destination):
    value = {"services": {name: {"image": digest} for name, digest in images.items()}}
    value["services"]["minio-init"] = {"image": images["minio"]}
    destination.write_text(json.dumps(value, indent=2))
    return Ops(ops.envfile, ops.project, [ROOT / "deploy/compose.production.yaml", destination])


def pull(digest):
    call(["docker", "pull", image(digest)])


def deploy(ops, args):
    current = saved(ops)
    if args.action == "install" and current.exists():
        raise SystemExit("Already installed; use apply")
    if args.action != "install" and not current.exists():
        raise SystemExit("No current production manifest; use install for an empty server")
    old = json.loads(current.read_text()) if current.exists() else None
    if old and old.get("project") != ops.project:
        raise SystemExit("Current manifest belongs to another project")
    previous = dict(old["images"]) if old else {}
    if args.action == "rollback":
        if not args.release_file or not args.schema_compatible:
            raise SystemExit(
                "Rollback requires --release-file and --schema-compatible; SQL downgrade is never automatic"
            )
        release = json.loads(args.release_file.read_text())
        if release.get("project") != ops.project or not release.get("before"):
            raise SystemExit(
                "Invalid rollback manifest or an initial installation with no previous release"
            )
        images = release["before"]
    else:
        images = {
            **previous,
            **dict.fromkeys(APPS, image(args.backend_image)),
            "nginx": image(args.web_image),
            "minio": image(args.storage_image),
        }
        for service, tag in INFRASTRUCTURE.items():
            if service not in images:
                call(["docker", "pull", tag])
                images[service] = image(
                    json.loads(
                        call(
                            [
                                "docker",
                                "image",
                                "inspect",
                                "--format",
                                "{{json .RepoDigests}}",
                                tag,
                            ],
                            capture=True,
                        )
                    )[0]
                )
    if set(images) != {*APPS, "minio", *INFRASTRUCTURE}:
        raise SystemExit("Incomplete image manifest")
    for digest in set(images.values()):
        pull(digest)
    current.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    current.parent.chmod(0o700)
    with ExitStack() as guards:
        lock = guards.enter_context((current.parent / "deployment.lock").open("w"))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if old and json.loads(current.read_text()) != old:
            raise SystemExit("Concurrent deployment changed the current manifest")
        if old:
            actual = {name: ops.container(name)["Image"] for name in APPS}
            expected = old.get("container_images")
            if actual != expected:
                raise SystemExit(
                    "Runtime differs from current manifest; reconcile before deployment"
                )
            backup(ops, ROOT / "backups" / ops.project)
        elif ops.rows():
            raise SystemExit("Initial installation requires an empty Compose project")
        elif call(
            [
                "docker",
                "volume",
                "ls",
                "--quiet",
                "--filter",
                f"label=com.docker.compose.project={ops.project}",
            ],
            capture=True,
        ).strip():
            raise SystemExit(
                "Existing project volumes require operator review; refusing to initialize over data"
            )
        backup_directory = ROOT / "backups" / ops.project
        backup_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        operation_lock = guards.enter_context((backup_directory / "operations.lock").open("w"))
        fcntl.flock(operation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if old and {name: ops.container(name)["Image"] for name in APPS} != old["container_images"]:
            raise SystemExit("Runtime changed after backup; refusing a concurrent rollout")
        configure(ops)
        identifier = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        state = current.parent / f"{identifier}.json"
        override = current.parent / f"{identifier}.compose.json"
        target = overlay(ops, images, override)
        manifest = {
            "project": ops.project,
            "before": previous,
            "images": images,
            "override": str(override),
            "status": "in_progress",
        }
        state.write_text(json.dumps(manifest, indent=2))
        try:
            target.compose("config", "--quiet")
            if not old:
                target.compose(
                    "up",
                    "-d",
                    "--no-build",
                    "--wait",
                    "--wait-timeout",
                    "180",
                    "postgres",
                    "redis",
                    "rabbitmq",
                )
                target.compose("up", "-d", "--no-build", "minio", "clamav")
                target.compose("run", "--rm", "--no-deps", "minio-init")
            if args.action != "rollback":
                for name in SERVICES:
                    target.compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        name,
                        "python",
                        "-m",
                        "libs.platform.migrate",
                        "upgrade",
                    )
                target.compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "media",
                    "python",
                    "-m",
                    "services.media.storage",
                )
                target.compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "people",
                    "python",
                    "-m",
                    "libs.platform.cli",
                    "seed-dictionaries",
                )
            target.compose(
                "up",
                "-d",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "180",
                *APPS,
                "minio",
            )
            for name in SERVICES:
                target.compose("exec", "-T", name, "python", "-m", "libs.platform.migrate", "check")
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                for url in [
                    ops.config["PUBLIC_URL"],
                    ops.config["S3_PUBLIC_ENDPOINT"] + "/minio/health/live",
                ]:
                    client.get(url).raise_for_status()
            target.pin_images()
            manifest["container_images"] = {name: target.container(name)["Image"] for name in APPS}
            manifest["status"] = "succeeded"
            temporary = current.with_suffix(".tmp")
            temporary.write_text(json.dumps(manifest, indent=2))
            os.replace(temporary, current)
        except BaseException:
            manifest["status"] = "failed_requires_operator_review"
            raise
        finally:
            state.write_text(json.dumps(manifest, indent=2))
        print(f"Verified production release: {state}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "plan",
            "install",
            "apply",
            "rollback",
            "status",
            "stop",
            "logs",
        ],
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.prod")
    parser.add_argument("--backend-image")
    parser.add_argument("--web-image")
    parser.add_argument("--storage-image")
    parser.add_argument("--release-file", type=Path)
    parser.add_argument("--schema-compatible", action="store_true")
    args = parser.parse_args()
    ops = Ops(args.env_file)
    if ops.config.get("SC_ENVIRONMENT") != "production":
        raise SystemExit("Production commands require SC_ENVIRONMENT=production")
    if args.action in {"status", "stop", "logs"}:
        state = json.loads(saved(ops).read_text())
        if state.get("project") != ops.project:
            raise SystemExit("Manifest belongs to another project")
        target = Ops(
            ops.envfile,
            ops.project,
            [ROOT / "deploy/compose.production.yaml", state["override"]],
        )
        if args.action == "status":
            target.compose("ps")
        elif args.action == "stop":
            target.compose("stop")
        else:
            target.compose("logs", "--tail", "100", "-f")
        return
    validate(ops)
    if args.action == "validate":
        print("Production configuration validated; secrets were not printed")
        return
    if args.action in {"plan", "install", "apply"}:
        for value in [args.backend_image, args.web_image, args.storage_image]:
            image(value)
    if args.action == "plan":
        print(
            json.dumps(
                {
                    "project": ops.project,
                    "backend": args.backend_image,
                    "web": args.web_image,
                    "storage": args.storage_image,
                    "backup_before_update": True,
                    "automatic_sql_downgrade": False,
                },
                indent=2,
            )
        )
        return
    deploy(ops, args)


if __name__ == "__main__":
    main()
