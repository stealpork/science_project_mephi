import importlib
import json
import os
from pathlib import Path

from libs.platform.config import SERVICES, get_settings

target = Path(__file__).resolve().parents[2] / "contracts/openapi"
target.mkdir(parents=True, exist_ok=True)
for service in SERVICES:
    os.environ["SC_SERVICE_NAME"] = service
    get_settings.cache_clear()
    app = importlib.import_module(f"services.{service}.main").app
    schema = app.openapi()

    schema["paths"] = {
        key: value for key, value in schema["paths"].items() if not key.startswith("/internal/")
    }
    (target / f"{service}.json").write_text(
        json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
print("Exported public OpenAPI schemas of all six services")
