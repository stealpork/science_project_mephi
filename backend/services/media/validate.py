import base64
import io
import json
import resource
import sys


def validate_pdf(data: bytes) -> bytes:
    from pypdf import PdfReader

    if not data.startswith(b"%PDF-"):
        raise ValueError("INVALID_PDF")
    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted or len(reader.pages) > 1000:
        raise ValueError("PDF_UNSUPPORTED")
    forbidden = {
        "/JavaScript",
        "/JS",
        "/Launch",
        "/OpenAction",
        "/AA",
        "/EmbeddedFiles",
        "/XFA",
        "/SubmitForm",
        "/ImportData",
    }
    visited: set[int] = set()
    count = 0

    def walk(item, depth=0):
        nonlocal count
        count += 1
        if depth > 60 or count > 50000:
            raise ValueError("PDF_TOO_COMPLEX")
        if hasattr(item, "get_object"):
            item = item.get_object()
        if id(item) in visited:
            return
        visited.add(id(item))
        if isinstance(item, dict):
            if forbidden & set(item):
                raise ValueError("UNSAFE_PDF")
            for value in item.values():
                walk(value, depth + 1)
        elif isinstance(item, (list, tuple)):
            for value in item:
                walk(value, depth + 1)

    walk(reader.trailer)
    return data


def validate_image(data: bytes) -> bytes:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = 20_000_000
    with Image.open(io.BytesIO(data)) as source:
        if source.format not in {"JPEG", "PNG", "WEBP"}:
            raise ValueError("INVALID_FILE")
        if source.width * source.height > 20_000_000:
            raise ValueError("IMAGE_TOO_LARGE")
        source.load()
        source.thumbnail((1200, 1200))
        clean = Image.new("RGB", source.size, (255, 255, 255))
        clean.paste(source.convert("RGB"))
        output = io.BytesIO()
        clean.save(output, "PNG")
        return output.getvalue()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    try:
        result: dict[str, bool | str]
        raw = sys.stdin.buffer.read(25_000_001)
        if len(raw) > 25_000_000:
            raise ValueError("FILE_TOO_LARGE")
        if sys.argv[1] == "pdf":
            validate_pdf(raw)
            result = {"valid": True}
        else:
            result = {"valid": True, "image": base64.b64encode(validate_image(raw)).decode()}
        print(json.dumps(result))
    except Exception as exc:
        code = (
            str(exc)
            if str(exc)
            in {
                "INVALID_PDF",
                "PDF_UNSUPPORTED",
                "PDF_TOO_COMPLEX",
                "UNSAFE_PDF",
                "IMAGE_TOO_LARGE",
                "FILE_TOO_LARGE",
            }
            else "INVALID_FILE"
        )
        print(json.dumps({"valid": False, "code": code}))
        sys.exit(1)
