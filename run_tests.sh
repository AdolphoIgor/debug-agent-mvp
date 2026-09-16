#!/usr/bin/env bash
set -euo pipefail

# Ensure execution occurs from the repository root directory
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_ROOT}"

echo "Validating execution environment..."
if command -v pytest >/dev/null 2>&1; then
    echo "Executing pytest suite directly within the active environment..."
    PYTHONPATH="${REPO_ROOT}/src" pytest -q "$@"
elif command -v docker >/dev/null 2>&1; then
    echo "Executing pytest suite within container: mvp_dev_workspace..."
    docker compose exec -T app pytest -q "$@"
else
    echo "Error: Neither local pytest nor Docker CLI was detected." >&2
    exit 1
fi

echo "All tests executed successfully."