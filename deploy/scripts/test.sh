#!/bin/sh
set -eu
SC_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$SC_ROOT/backend"
require_test_target() {
    uv run --frozen python -c 'import sys; from tests.conftest import validate_live_target; validate_live_target(sys.argv[1])' "$SC_ROOT/.env.test"
}
test_compose() {
    require_test_target
    docker compose --env-file "$SC_ROOT/.env.test" -p science-test -f "$SC_ROOT/deploy/compose.yaml" -f "$SC_ROOT/deploy/compose.local.yaml" "$@"
}
restart_test_apps() {
    test_compose start --wait identity people projects publications media activity identity-worker people-worker projects-worker publications-worker media-worker activity-worker
}
restore_test_http() {
    "$SC_ROOT/deploy/scripts/test.sh" perf-up
    test_compose up -d --no-deps nginx minio
}
case "${1:-unit}" in
    clean) require_test_target
        uv run --frozen python -m tests.cleanup --env-file "$SC_ROOT/.env.test" ;;
    fresh)
        if [ -f "$SC_ROOT/.env.test" ]; then
            test_compose stop -t 30 identity people projects publications media activity identity-worker people-worker projects-worker publications-worker media-worker activity-worker
        fi
        if [ -d "$SC_ROOT/test-results/coverage" ]; then
            SC_COVERAGE_ARCHIVE=$(mktemp -d "$SC_ROOT/test-results/coverage-history.XXXXXX")
            mv "$SC_ROOT/test-results/coverage" "$SC_COVERAGE_ARCHIVE/coverage"
        fi
        mkdir -p "$SC_ROOT/test-results/coverage"
        "$SC_ROOT/deploy/scripts/test.sh" up
        ;;
    unit) mkdir -p "$SC_ROOT/test-results/coverage"
        COVERAGE_FILE="$SC_ROOT/test-results/coverage/.coverage.unit" uv run --frozen pytest tests/unit --tb=short --junitxml=../test-results/unit.xml --cov=services --cov=libs --cov-report=term-missing --cov-report=xml:../test-results/unit-coverage.xml ;;
    live) uv run --frozen pytest --live tests/integration tests/e2e --tb=short --junitxml=../test-results/live.xml ;;
    tls)
        require_test_target
        uv run --frozen python ../deploy/scripts/tls.py configure --env-file "$SC_ROOT/.env.test"
        trap restore_test_http EXIT
        COMPOSE_PROJECT_NAME=science-test SC_LOCAL=1 SC_ENV_FILE="$SC_ROOT/.env.test" SC_COMPOSE_EXTRA="$SC_ROOT/deploy/compose.tls.yaml" "$SC_ROOT/deploy/scripts/dev.sh" up
        uv run --frozen python ../deploy/scripts/tls.py drill --env-file "$SC_ROOT/.env.test"
        trap - EXIT
        restore_test_http
        ;;
    perf-up)
        test_compose build identity
        test_compose up -d --wait --wait-timeout 180 identity people projects publications media activity identity-worker people-worker projects-worker publications-worker media-worker activity-worker
        uv run --frozen python ../deploy/scripts/ops.py pin-images --env-file "$SC_ROOT/.env.test" ;;
    up) uv run --frozen python ../deploy/scripts/init.py --environment test
        require_test_target
        mkdir -p "$SC_ROOT/test-results/coverage"
        COMPOSE_PROJECT_NAME=science-test SC_LOCAL=1 SC_ENV_FILE="$SC_ROOT/.env.test" SC_COMPOSE_EXTRA="$SC_ROOT/deploy/compose.test.yaml" "$SC_ROOT/deploy/scripts/dev.sh" up ;;
    coverage)
        test_compose stop -t 30 identity people projects publications media activity identity-worker people-worker projects-worker publications-worker media-worker activity-worker
        trap restart_test_apps EXIT
        uv run --frozen coverage combine --keep "$SC_ROOT/test-results/coverage"
        uv run --frozen coverage report
        uv run --frozen coverage xml -o "$SC_ROOT/test-results/coverage.xml"
        uv run --frozen coverage json -o "$SC_ROOT/test-results/coverage.json"
        uv run --frozen python "$SC_ROOT/deploy/scripts/check_coverage.py" "$SC_ROOT/test-results/coverage.json"
        trap - EXIT
        restart_test_apps ;;
    *) echo 'Usage: test.sh unit|up|fresh|live|clean|coverage|perf-up|tls'; exit 2 ;;
esac
