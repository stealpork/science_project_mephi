#!/bin/sh
set -eu
SC_WEB_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export SC_COMPOSE_EXTRA="$SC_WEB_ROOT/deploy/compose.local.yaml"
export SC_ENV_FILE=${SC_ENV_FILE:-"$SC_WEB_ROOT/.env"}
case "${1:-help}" in
    up) exec "$SC_WEB_ROOT/deploy/scripts/dev.sh" up ;;
    stop)
        docker compose --env-file "$SC_ENV_FILE" -f "$SC_WEB_ROOT/deploy/compose.yaml" -f "$SC_COMPOSE_EXTRA" stop ;;
    test-live)
        cd "$SC_WEB_ROOT/frontend"
        exec npm run test:live ;;
    *) printf '%s\n' 'Usage: sh deploy/scripts/web.sh up|stop|test-live (SC_ENV_FILE=.env.test for isolated tests)'; exit 2 ;;
esac
