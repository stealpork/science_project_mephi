#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
case "${1:-up}" in
    up)
        uv run --project "$SC_ROOT/backend" --frozen python "$SC_ROOT/deploy/scripts/alerting.py" configure --env-file "$SC_ENV_FILE"
        compose --profile monitoring up -d --wait --wait-timeout 120 prometheus grafana backup-metrics alertmanager
        compose --profile monitoring up -d --no-deps --force-recreate --wait alertmanager
        compose restart prometheus
        ;;
    drill)
        exec uv run --project "$SC_ROOT/backend" --frozen python "$SC_ROOT/deploy/scripts/alerting.py" drill --env-file "$SC_ENV_FILE"
        ;;
    *) echo 'Usage: monitoring.sh up|drill'; exit 2 ;;
esac
