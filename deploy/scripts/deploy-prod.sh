#!/bin/sh
set -eu
SC_PROD_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$SC_PROD_ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
exec uv run --project "$SC_PROD_ROOT/backend" --frozen python "$SC_PROD_ROOT/deploy/scripts/production.py" "$@"
