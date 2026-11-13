# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.13-slim-bookworm AS base
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY backend/libs/ ./libs/
COPY backend/services/ ./services/
COPY backend/migrations/ ./migrations/
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "libs.platform.serve"]

FROM base AS test
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY backend/tests/ ./tests/
USER 10001:10001
CMD ["python", "-m", "libs.platform.serve"]
