#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
case "${1:-up}" in
    up)
        compose build identity nginx
        if ! docker image inspect science-minio:2025-09-07 >/dev/null 2>&1; then
            compose build minio
        fi
        compose up -d --wait --wait-timeout 180 postgres redis rabbitmq mailpit
        compose up -d minio clamav
        uv run --directory "$SC_ROOT/backend" --frozen python "$SC_ROOT/deploy/scripts/ops.py" pin-images --env-file "$SC_ENV_FILE" --service minio
        compose run --rm minio-init
        for sc_service in identity people projects publications media activity; do
            compose run --rm --no-deps "$sc_service" python -m libs.platform.migrate upgrade
        done
        compose run --rm --no-deps media python -m services.media.storage
        compose run --rm --no-deps people python -m libs.platform.cli seed-dictionaries
        compose up -d --wait --wait-timeout 180 identity people projects publications media activity identity-worker people-worker projects-worker publications-worker media-worker activity-worker nginx
        uv run --directory "$SC_ROOT/backend" --frozen python "$SC_ROOT/deploy/scripts/ops.py" pin-images --env-file "$SC_ENV_FILE"
        ;;
    down) compose down ;;
    logs) shift; compose logs --tail 100 -f "$@" ;;
    ps) compose ps ;;
    *) printf '%s\n' 'Usage: dev.sh up|down|logs|ps'; exit 2 ;;
esac
