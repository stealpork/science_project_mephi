#!/bin/sh
set -eu
SC_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
uv run --directory "$SC_ROOT/backend" --frozen python -c 'from tests.conftest import validate_live_target, verify_live_runtime; verify_live_runtime(validate_live_target())'
mkdir -p "$SC_ROOT/test-results"
SC_LOAD_SCRIPT=catalogs.js
case "${1:-smoke}" in
    workflow) SC_LOAD_SCRIPT=workflow.js ;;
    smoke|baseline|stress|soak) ;;
    *) echo 'Usage: load.sh smoke|baseline|stress|soak|workflow'; exit 2 ;;
esac
clean_load_data() {
    SC_LOAD_EXIT=$?
    trap - EXIT
    uv run --directory "$SC_ROOT/backend" --frozen python -m tests.cleanup || exit 1
    exit "$SC_LOAD_EXIT"
}
trap clean_load_data EXIT
docker run --rm --network host --user "$(id -u):$(id -g)" \
  -e "LOAD_PROFILE=${1:-smoke}" -e "BASE_URL=${TEST_BASE_URL:-http://127.0.0.1:18081}" \
  -e DEMO_PASSWORD \
  -v "$SC_ROOT/backend/tests/load:/scripts:ro" -v "$SC_ROOT/test-results:/results" \
  grafana/k6:1.0.0 run --quiet --summary-export "/results/load-${1:-smoke}.json" "/scripts/$SC_LOAD_SCRIPT"
