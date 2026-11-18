import asyncio
import importlib
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def tls(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "deploy/scripts"))
    return importlib.import_module("tls")


async def test_generated_certificate_has_a_trusted_strict_tls_handshake(tls, tmp_path):
    ops = SimpleNamespace(config={"SECRET_DIRECTORY": str(tmp_path), "SC_ENVIRONMENT": "test"})
    tls.configure(ops)
    directory = tls.directory(ops)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(directory / "server/server.crt", directory / "server/server.key")
    trust = ssl.create_default_context(cafile=str(directory / "ca.crt"))

    async def handle(reader, writer):
        writer.write(b"verified")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_context)
    try:
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=trust, server_hostname="localhost"
        )
        assert await reader.read() == b"verified"
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
    assert (directory / "server/server.key").stat().st_mode & 0o777 == 0o600
    assert "listen 443 ssl" in (directory / "nginx.conf").read_text()
    original = (directory / "server/server.key").read_bytes()
    tls.configure(ops)
    assert (directory / "server/server.key").read_bytes() == original


def test_production_never_silently_creates_self_signed_certificates(tls, tmp_path):
    with pytest.raises(SystemExit, match="real server"):
        tls.configure(
            SimpleNamespace(
                config={"SECRET_DIRECTORY": str(tmp_path), "SC_ENVIRONMENT": "production"}
            )
        )
    assert not (tmp_path / "tls/ca.key").exists()


def test_incomplete_certificate_material_is_preserved(tls, tmp_path):
    directory = tmp_path / "tls/server"
    directory.mkdir(parents=True)
    (directory / "server.key").write_text("existing-key")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        tls.configure(SimpleNamespace(config={"SECRET_DIRECTORY": str(tmp_path)}))
    assert (directory / "server.key").read_text() == "existing-key"
