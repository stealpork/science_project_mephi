import uvicorn

from libs.platform.config import get_settings

if __name__ == "__main__":
    service = get_settings().service_name
    uvicorn.run(
        f"services.{service}.main:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=False,
        access_log=False,
        limit_concurrency=64,
        backlog=256,
    )
