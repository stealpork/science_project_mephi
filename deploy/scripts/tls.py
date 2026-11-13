import argparse
import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import os
import secrets
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from ops import ROOT, Ops
from PIL import Image


def directory(ops):
    secret = Path(ops.config["SECRET_DIRECTORY"])
    return (secret if secret.is_absolute() else ROOT / "deploy" / secret) / "tls"


def configure(ops):
    target = directory(ops)
    server = target / "server"
    certificate, private = server / "server.crt", server / "server.key"
    if ops.config.get("SC_ENVIRONMENT") == "production" and (target / "ca.key").exists():
        raise SystemExit("Do not use the generated development CA for production")
    if not certificate.exists() or not private.exists():
        if any(
            path.exists() for path in [certificate, private, target / "ca.key", target / "ca.crt"]
        ):
            raise SystemExit("Incomplete existing TLS material; refusing to overwrite keys")
        if ops.config.get("SC_ENVIRONMENT") == "production":
            raise SystemExit(
                "Supply a real server.crt/server.key for production; local CA is test-only"
            )
        server.mkdir(parents=True, mode=0o700)
        issued = datetime.now(UTC)
        ca_key, key = (
            ec.generate_private_key(ec.SECP256R1()),
            ec.generate_private_key(ec.SECP256R1()),
        )
        issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Science local development CA")]
        )
        ca = (
            x509.CertificateBuilder()
            .subject_name(issuer)
            .issuer_name(issuer)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(issued - timedelta(minutes=5))
            .not_valid_after(issued + timedelta(days=31))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(issued - timedelta(minutes=5))
            .not_valid_after(issued + timedelta(days=30))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(True, False, False, False, False, False, False, False, False),
                critical=True,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        for path, value in [(target / "ca.key", ca_key), (private, key)]:
            path.write_bytes(
                value.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            path.chmod(0o600)
        (target / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    certificate_value = x509.load_pem_x509_certificate(certificate.read_bytes())
    key_value = serialization.load_pem_private_key(private.read_bytes(), password=None)
    if certificate_value.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != key_value.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise SystemExit("TLS certificate and private key do not match")
    if certificate_value.not_valid_after_utc <= datetime.now(UTC):
        raise SystemExit("TLS certificate has expired")
    if certificate_value.not_valid_before_utc > datetime.now(UTC):
        raise SystemExit("TLS certificate is not active yet")
    config = (ROOT / "deploy/nginx/nginx.conf").read_text()
    tls = "\n        ssl_certificate /etc/science-tls/server.crt;\n        ssl_certificate_key /etc/science-tls/server.key;\n        ssl_protocols TLSv1.2 TLSv1.3;\n        ssl_session_tickets off;"
    config = config.replace("listen 80;", "listen 80;\n        listen 443 ssl;" + tls)
    config = config.replace("listen 9000;", "listen 9000;\n        listen 9443 ssl;" + tls)
    (target / "nginx.conf").write_text(config)
    print("TLS material checked and Nginx config rendered; private keys were not printed")


async def drill(ops):
    if ops.project != "science-test" or ops.config.get("SC_ENVIRONMENT") != "test":
        raise SystemExit("Synthetic TLS writes are restricted to science-test")
    trust = ssl.create_default_context(cafile=str(directory(ops) / "ca.crt"))
    base = f"https://localhost:{ops.config.get('TLS_APP_PORT', '18443')}"
    email = f"tls-{uuid4().hex}@example.org"
    password = secrets.token_urlsafe(32)
    async with httpx.AsyncClient(base_url=base, verify=trust, timeout=30) as client:
        preflight = await client.options(
            "/api/v1/media/uploads",
            headers={
                "Origin": base,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
            },
        )
        assert (
            preflight.status_code == 200
            and preflight.headers["access-control-allow-origin"] == base
        )
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "first_name": "TLS",
                "last_name": "Drill",
            },
        )
        response.raise_for_status()
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": email,
                "password": password,
            },
        )
        response.raise_for_status()
        pair = response.json()
        headers = {"Authorization": "Bearer " + pair["access_token"]}
        user = pair["account"]["user_id"]
        for _ in range(150):
            response = await client.get("/api/v1/me/profile", headers=headers)
            if response.status_code == 200:
                response.raise_for_status()
                break
            if response.status_code not in {202, 404}:
                response.raise_for_status()
            await asyncio.sleep(0.2)
        else:
            raise SystemExit("Profile creation did not complete in the isolated TLS drill")
        stream = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(stream, "PNG")
        data = stream.getvalue()
        response = await client.post(
            "/api/v1/media/uploads",
            headers={**headers, "Idempotency-Key": uuid4().hex},
            json={
                "purpose": "avatar",
                "resource_id": user,
                "filename": "tls-avatar.png",
                "content_type": "image/png",
                "size_bytes": len(data),
                "checksum_sha256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
            },
        )
        response.raise_for_status()
        upload = response.json()
        assert upload["upload_url"].startswith("https://localhost:")
        preflight = await client.options(
            upload["upload_url"],
            headers={
                "Origin": base,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-amz-checksum-sha256",
            },
        )
        assert (
            preflight.status_code in {200, 204}
            and preflight.headers["access-control-allow-origin"] == base
        )
        response = await client.put(
            upload["upload_url"], content=data, headers=upload["required_headers"]
        )
        response.raise_for_status()
        response = await client.post(
            f"/api/v1/media/uploads/{upload['upload_id']}/complete",
            headers={**headers, "Idempotency-Key": uuid4().hex},
        )
        response.raise_for_status()
        for _ in range(150):
            response = await client.get(
                f"/api/v1/media/uploads/{upload['upload_id']}", headers=headers
            )
            response.raise_for_status()
            state = response.json()
            if state["status"] in {"ready", "rejected"}:
                break
            await asyncio.sleep(0.2)
        assert state["status"] == "ready"
        profile = (await client.get("/api/v1/me/profile", headers=headers)).json()
        response = await client.patch(
            "/api/v1/me/profile",
            headers=headers,
            json={"version": profile["version"], "avatar_file_id": upload["file_id"]},
        )
        response.raise_for_status()
        response = await client.get(f"/api/v1/users/{user}/avatar-url")
        response.raise_for_status()
        response = await client.get(response.json()["url"])
        response.raise_for_status()
        assert response.headers["content-type"] == "image/png"
    (ROOT / "test-results/tls.json").write_text(
        json.dumps(
            {
                "success": True,
                "certificate_verification": True,
                "flow": "HTTPS JWT -> signed S3 PUT -> ClamAV -> bind -> HTTPS S3 GET",
                "at": datetime.now(UTC).isoformat(),
            }
        )
    )
    print("Verified HTTPS API and signed S3 upload/download with certificate validation enabled")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["configure", "drill"])
    parser.add_argument("--env-file", default=ROOT / ".env.test")
    args = parser.parse_args()
    os.umask(0o077)
    ops = Ops(args.env_file)
    try:
        configure(ops) if args.action == "configure" else asyncio.run(drill(ops))
    except httpx.HTTPStatusError as exc:
        try:
            code = exc.response.json().get("error", {}).get("code", "HTTP_ERROR")
        except (ValueError, AttributeError):
            code = "HTTP_ERROR"
        raise SystemExit(
            f"TLS drill failed: HTTP {exc.response.status_code}, path={exc.request.url.path}, code={code}; query tokens were not printed"
        ) from None
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"TLS drill failed: {type(exc).__name__}; signed URLs were not printed"
        ) from None


if __name__ == "__main__":
    main()
