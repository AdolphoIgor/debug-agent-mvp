#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_ROOT}"

echo "Validating execution environment..."

TARGET_PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

if command -v pytest >/dev/null 2>&1; then
    echo "Executing pytest suite directly within the active environment..."
    PYTHONPATH="${TARGET_PYTHONPATH}" pytest -q "$@"
elif command -v docker >/dev/null 2>&1; then
    echo "Executing pytest suite via Docker Compose..."
    CONTAINER_PYTHONPATH="/workspace:/workspace/src"
    
    # Verify if service 'app' is actively running
    if docker compose ps --services --filter "status=running" 2>/dev/null | grep -qx "app"; then
        echo "Active container detected. Dispatching via exec..."
        docker compose exec -T -e PYTHONPATH="${CONTAINER_PYTHONPATH}" app pytest -q "$@"
    else
        echo "Active container not detected. Dispatching via ephemeral run..."
        docker compose run --rm -T -e PYTHONPATH="${CONTAINER_PYTHONPATH}" app pytest -q "$@"
    fi
else
    echo "Error: Neither local pytest nor Docker CLI was detected." >&2
    exit 1
fi

echo "All tests executed successfully."