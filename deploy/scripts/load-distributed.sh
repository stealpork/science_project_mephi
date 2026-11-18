#!/bin/sh
set -eu
SC_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
uv run --directory "$SC_ROOT/backend" --frozen python -c 'from tests.conftest import validate_live_target, verify_live_runtime; verify_live_runtime(validate_live_target())'
SC_PROFILE=${1:-baseline}
SC_SCALE=1
case "$SC_PROFILE" in
    baseline|soak) ;;
    stress) SC_SCALE=0.2 ;;
    *) echo 'Usage: load-distributed.sh baseline|stress|soak'; exit 2 ;;
esac
SC_NETWORK=science-test_application
SC_OWNER=$(docker network inspect --format '{{index .Labels "com.docker.compose.project"}}' "$SC_NETWORK")
if [ "$SC_OWNER" != science-test ]; then
    echo 'Refusing a network outside the isolated science-test project' >&2
    exit 1
fi
clean_load_data() {
    SC_LOAD_EXIT=$?
    trap - EXIT
    uv run --directory "$SC_ROOT/backend" --frozen python -m tests.cleanup || exit 1
    exit "$SC_LOAD_EXIT"
}
trap clean_load_data EXIT
mkdir -p "$SC_ROOT/test-results"
SC_PIDS=''
SC_NAMES=''
SC_STAMP=$(date +%s)
cleanup() {
    trap - INT TERM HUP
    for SC_NAME in $SC_NAMES; do
        docker stop -t 3 "$SC_NAME" >/dev/null 2>&1 || true
    done
    exit 130
}
trap cleanup INT TERM HUP
for SC_WORKER in 1 2 3 4 5; do
    SC_NAME="science-load-$SC_STAMP-$$-$SC_WORKER"
    SC_NAMES="$SC_NAMES $SC_NAME"
    docker run --rm --name "$SC_NAME" --network "$SC_NETWORK" --user "$(id -u):$(id -g)" \
      -e "LOAD_PROFILE=$SC_PROFILE" -e "VU_SCALE=$SC_SCALE" -e BASE_URL=http://nginx \
      -v "$SC_ROOT/backend/tests/load:/scripts:ro" -v "$SC_ROOT/test-results:/results" \
      grafana/k6:1.0.0 run --quiet --summary-export "/results/distributed-$SC_PROFILE-$SC_WORKER.json" /scripts/catalogs.js &
    SC_PIDS="$SC_PIDS $!"
done
SC_RESULT=0
for SC_PID in $SC_PIDS; do
    wait "$SC_PID" || SC_RESULT=1
done
exit "$SC_RESULT"
