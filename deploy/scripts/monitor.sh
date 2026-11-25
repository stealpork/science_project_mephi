#!/bin/sh
set -eu
SC_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
exec uv run --project "$SC_ROOT/backend" --frozen python "$SC_ROOT/deploy/scripts/ops.py" status "$@"
