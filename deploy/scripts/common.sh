#!/bin/sh
set -eu
SC_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
SC_ENV_FILE=${SC_ENV_FILE:-"$SC_ROOT/.env"}
if [ ! -f "$SC_ENV_FILE" ]; then
    printf '%s\n' 'No configuration. Run: cd backend && uv run python ../deploy/scripts/init.py' >&2
    exit 1
fi
SC_ENV_FILE=$(realpath -- "$SC_ENV_FILE")
if [ -n "${SC_COMPOSE_EXTRA:-}" ]; then
    SC_COMPOSE_EXTRA=$(realpath -- "$SC_COMPOSE_EXTRA")
fi
compose() {
    if [ "${SC_LOCAL:-0}" = 1 ]; then
        if [ -n "${SC_COMPOSE_EXTRA:-}" ]; then
            docker compose --env-file "$SC_ENV_FILE" -f "$SC_ROOT/deploy/compose.yaml" -f "$SC_ROOT/deploy/compose.local.yaml" -f "$SC_COMPOSE_EXTRA" "$@"
        else
            docker compose --env-file "$SC_ENV_FILE" -f "$SC_ROOT/deploy/compose.yaml" -f "$SC_ROOT/deploy/compose.local.yaml" "$@"
        fi
    elif [ -n "${SC_COMPOSE_EXTRA:-}" ]; then
        docker compose --env-file "$SC_ENV_FILE" -f "$SC_ROOT/deploy/compose.yaml" -f "$SC_COMPOSE_EXTRA" "$@"
    else
        docker compose --env-file "$SC_ENV_FILE" -f "$SC_ROOT/deploy/compose.yaml" "$@"
    fi
}
