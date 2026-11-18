import base64
import hashlib
import io
import json
import resource
import runpy
import sys
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from PIL import Image

from libs.platform.config import Settings
from libs.platform.errors import APIError
from services.media import events, storage, validate
from tests.files import pdf_bytes
from tests.unit.support import database, request, rows

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("production", [False, True])
async def test_scanner_bypass_only_in_non_production(production):
    settings = Settings()
    settings.scanner_required = False
    settings.environment = "production" if production else "test"
    if production:
        with pytest.raises(RuntimeError, match="cannot be disabled"):
            await events.antivirus(settings, b"data")
    else:
        assert await events.antivirus(settings, b"data") is None


@pytest.mark.parametrize(
    "response,error",
    [(b"stream: OK\0", None), (b"stream: FOUND\0", ValueError), (b"garbled", RuntimeError)],
)
async def test_scanner_protocol_closes_socket_on_every_result(monkeypatch, response, error):
    reader, writer = (
        Obj(read=AsyncMock(return_value=response)),
        Obj(write=MagicMock(), drain=AsyncMock(), close=MagicMock(), wait_closed=AsyncMock()),
    )
    monkeypatch.setattr(events.asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))
    if error:
        with pytest.raises(error):
            await events.antivirus(Settings(), b"a" * 65537)
    else:
        await events.antivirus(Settings(), b"a" * 65537)
    assert writer.write.call_args_list[0].args == (b"zINSTREAM\0",)
    assert writer.write.call_args_list[-1].args == (b"\0\0\0\0",)
    assert len(writer.write.call_args_list[1].args[0]) == 65540
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.parametrize("state", [None, "ready", "expired"])
async def test_worker_does_not_reprocess_terminal_or_missing_file(monkeypatch, state):
    db = database()
    db.scalar.return_value = Obj(status=state) if state else None
    call = AsyncMock()
    monkeypatch.setattr(events, "s3_call", call)
    await events.handle(request(), db, {"payload": {"file_id": "f"}})
    call.assert_not_awaited()


@pytest.mark.parametrize(
    "fault", ["checksum", "length", "timeout", "parser-invalid", "parser-no-code", "valid"]
)
@pytest.mark.parametrize("avatar", [False, True])
async def test_file_scanning_rejects_corruption_timeout_and_parser_errors(
    monkeypatch, fault, avatar
):
    content, clean = b"untrusted data", b"clean image"
    row = Obj(
        id="file",
        status="uploaded",
        size_bytes=len(content) + int(fault == "length"),
        checksum_sha256="wrong"
        if fault == "checksum"
        else base64.b64encode(hashlib.sha256(content).digest()).decode(),
        purpose="avatar" if avatar else "paper",
        quarantine_key="quarantine",
        object_version="pinned",
        content_type="image/jpeg" if avatar else "application/pdf",
        filename="input",
        user_id="owner",
        reservation_id="reservation",
    )
    req, db = request(), database()
    db.scalar.return_value = row
    body = Obj(read=MagicMock(return_value=content), close=MagicMock())
    s3 = AsyncMock(return_value={"Body": body})
    monkeypatch.setattr(events, "s3_call", s3)
    monkeypatch.setattr(events, "antivirus", AsyncMock())
    parser = {
        "valid": fault not in {"parser-invalid", "parser-no-code"},
        "image": base64.b64encode(clean).decode(),
    }
    if fault == "parser-invalid":
        parser["code"] = "UNSAFE_PDF"
    process = Obj(
        communicate=AsyncMock(
            return_value=(json.dumps(parser).encode(), b""),
            side_effect=TimeoutError if fault == "timeout" else None,
        ),
        kill=MagicMock(),
        wait=AsyncMock(),
    )
    monkeypatch.setattr(events.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    emit = AsyncMock()
    monkeypatch.setattr(events, "append_event", emit)
    await events.handle(req, db, {"payload": {"file_id": "file"}})
    body.close.assert_called_once()
    assert s3.call_args_list[0].kwargs["VersionId"] == "pinned"
    if fault == "valid":
        assert row.status == "ready" and row.final_key.endswith(".png" if avatar else ".pdf")
        assert s3.call_args_list[1].kwargs["Body"] == (clean if avatar else content)
        assert emit.call_args.args[1] == "media.file_ready"
    else:
        assert row.status == "rejected" and emit.call_args.args[1] == "media.file_rejected"
        assert (
            row.rejection_code
            == {
                "checksum": "CHECKSUM_MISMATCH",
                "length": "CHECKSUM_MISMATCH",
                "timeout": "FILE_PROCESSING_TIMEOUT",
                "parser-invalid": "UNSAFE_PDF",
                "parser-no-code": "INVALID_FILE",
            }[fault]
        )
        assert s3.await_count == 1
        if fault == "timeout":
            process.kill.assert_called_once()
            process.wait.assert_awaited_once()


async def test_reconcile_expires_and_requeues_unfinished_files(monkeypatch):
    db = database()
    expired = Obj(id="expired", status="scanning", reservation_id="r")
    db.scalars.side_effect = [rows([expired]), rows([Obj(id="unfinished")])]
    emit = AsyncMock()
    monkeypatch.setattr(events, "append_event", emit)
    await events.reconcile(request(), db)
    assert expired.status == "expired"
    assert [call.args[1] for call in emit.call_args_list] == [
        "media.upload_expired",
        "media.upload_completed",
    ]


@pytest.mark.parametrize("fault", ["encrypted", "many-pages", "deep", "wide", "cycle", "list"])
def test_pdf_graph_limits_and_cycle_detection(monkeypatch, fault):
    trailer = {}
    if fault == "deep":
        for _ in range(62):
            trailer = {"child": trailer}
    elif fault == "wide":
        trailer = {"items": list(range(50001))}
    elif fault == "cycle":
        trailer["self"] = trailer
    elif fault == "list":
        trailer = {"items": [1, 2, (3, 4)]}
    import pypdf

    monkeypatch.setattr(
        pypdf,
        "PdfReader",
        lambda *args, **kwargs: Obj(
            is_encrypted=fault == "encrypted",
            pages=range(1001 if fault == "many-pages" else 1),
            trailer=trailer,
        ),
    )
    if fault in {"cycle", "list"}:
        assert validate.validate_pdf(b"%PDF-test") == b"%PDF-test"
    else:
        with pytest.raises(ValueError) as error:
            validate.validate_pdf(b"%PDF-test")
        assert str(error.value) == (
            "PDF_UNSUPPORTED" if fault in {"encrypted", "many-pages"} else "PDF_TOO_COMPLEX"
        )


@pytest.mark.parametrize(
    "format,size,code", [("GIF", (2, 2), "INVALID_FILE"), ("PNG", (5000, 4001), "IMAGE_TOO_LARGE")]
)
def test_image_format_and_pixel_limit(format, size, code):
    output = io.BytesIO()
    Image.new("L", size).save(output, format)
    if code == "IMAGE_TOO_LARGE":
        with pytest.warns(Image.DecompressionBombWarning), pytest.raises(ValueError, match=code):
            validate.validate_image(output.getvalue())
    else:
        with pytest.raises(ValueError, match=code):
            validate.validate_image(output.getvalue())


@pytest.mark.parametrize("kind", ["pdf", "image", "invalid", "too-large"])
def test_parser_cli_has_limits_and_machine_readable_result(monkeypatch, capsys, kind):
    data = (
        pdf_bytes()
        if kind == "pdf"
        else b"bad"
        if kind == "invalid"
        else b"x" * 25_000_001
        if kind == "too-large"
        else None
    )
    if data is None:
        image = io.BytesIO()
        Image.new("RGB", (2, 2)).save(image, "PNG")
        data = image.getvalue()
    monkeypatch.setattr(
        sys, "argv", ["validate", "pdf" if kind in {"pdf", "invalid", "too-large"} else "image"]
    )
    monkeypatch.setattr(sys, "stdin", Obj(buffer=io.BytesIO(data)))
    limits = MagicMock()
    monkeypatch.setattr(resource, "setrlimit", limits)
    monkeypatch.delitem(sys.modules, "services.media.validate", raising=False)
    if kind in {"invalid", "too-large"}:
        with pytest.raises(SystemExit) as error:
            runpy.run_module("services.media.validate", run_name="__main__")
        assert error.value.code == 1
    else:
        runpy.run_module("services.media.validate", run_name="__main__")
    result = json.loads(capsys.readouterr().out)
    assert result["valid"] is (kind in {"pdf", "image"})
    if kind == "too-large":
        assert result["code"] == "FILE_TOO_LARGE"
    assert limits.call_args_list[0].args == (
        resource.RLIMIT_AS,
        (384 * 1024 * 1024, 384 * 1024 * 1024),
    )


async def test_storage_translates_sdk_failures(monkeypatch):
    monkeypatch.setattr(
        storage,
        "s3_client",
        lambda _: Obj(
            get_object=MagicMock(side_effect=EndpointConnectionError(endpoint_url="http://offline"))
        ),
    )
    with pytest.raises(APIError) as error:
        await storage.s3_call(Settings(), "get_object", Bucket="bucket")
    assert error.value.status == 503 and error.value.code == "STORAGE_UNAVAILABLE"


async def test_storage_initializes_missing_buckets_and_cors(monkeypatch):
    client = Obj(
        head_bucket=MagicMock(side_effect=ClientError({"Error": {"Code": "404"}}, "HeadBucket")),
        create_bucket=MagicMock(),
        put_bucket_versioning=MagicMock(),
        put_bucket_cors=MagicMock(),
        put_bucket_lifecycle_configuration=MagicMock(),
    )
    monkeypatch.setattr(storage, "s3_client", lambda _: client)
    await storage.initialize(Settings(s3_manage_cors=True))
    assert client.create_bucket.call_count == 2 and client.put_bucket_cors.call_count == 2
    assert all(
        call.kwargs["VersioningConfiguration"] == {"Status": "Enabled"}
        for call in client.put_bucket_versioning.call_args_list
    )
    assert (
        client.put_bucket_lifecycle_configuration.call_args.kwargs["LifecycleConfiguration"][
            "Rules"
        ][0]["Expiration"]["Days"]
        == 2
    )


def test_storage_module_entrypoint(monkeypatch):
    def run(coroutine):
        assert coroutine.cr_code.co_name == "initialize"
        coroutine.close()

    monkeypatch.setattr(storage.asyncio, "run", run)
    monkeypatch.delitem(sys.modules, "services.media.storage", raising=False)
    runpy.run_module("services.media.storage", run_name="__main__")
