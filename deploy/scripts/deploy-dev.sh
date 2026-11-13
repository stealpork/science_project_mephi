#!/bin/sh
set -eu
SC_DEV_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export SC_LOCAL=1
export SC_ENV_FILE=${SC_ENV_FILE:-"$SC_DEV_ROOT/.env"}
unset COMPOSE_PROJECT_NAME
require_dev_config() {
    uv run --project "$SC_DEV_ROOT/backend" --frozen python -c '
import re
import sys
from dotenv import dotenv_values
config = dotenv_values(sys.argv[1])
if config.get("SC_ENVIRONMENT") not in {"development", "test"} or not re.fullmatch("science(?:-[a-z0-9-]+)?", config.get("COMPOSE_PROJECT_NAME") or "science"):
    raise SystemExit("Development deployment cannot target production or an unrelated Compose project")
' "$SC_ENV_FILE"
}
case "${1:-up}" in
    up)
        if [ ! -f "$SC_ENV_FILE" ]; then
            if [ "$SC_ENV_FILE" != "$SC_DEV_ROOT/.env" ]; then
                printf '%s\n' 'Custom configuration does not exist; refusing to generate a different target' >&2
                exit 1
            fi
            uv run --project "$SC_DEV_ROOT/backend" --frozen python "$SC_DEV_ROOT/deploy/scripts/init.py"
        fi
        require_dev_config
        exec "$SC_DEV_ROOT/deploy/scripts/dev.sh" up ;;
    stop|status|logs)
        require_dev_config
        . "$SC_DEV_ROOT/deploy/scripts/common.sh"
        case "$1" in
            stop) compose stop ;;
            status) compose ps ;;
            logs) shift; compose logs --tail 100 -f "$@" ;;
        esac ;;
    demo)
        require_dev_config
        exec uv run --project "$SC_DEV_ROOT/backend" --frozen python "$SC_DEV_ROOT/deploy/scripts/demo.py" --env-file "$SC_ENV_FILE" ;;
    *) printf '%s\n' 'Usage: deploy-dev.sh up|demo|stop|status|logs [service]'; exit 2 ;;
esac
