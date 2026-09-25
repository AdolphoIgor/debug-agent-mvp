#!/usr/bin/env bash
set -euo pipefail

# TARGET_PROJECT: DEBUG-AGENT-MVP
# Execution harness for manual and CI-driven test execution.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# Verification of required binaries
if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' package manager was not found in PATH." >&2
    echo "Installation can be performed via: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# Synchronization of dependencies including development packages
echo "==> Synchronizing dependencies with development extras..."
uv sync --extra dev --quiet

# Export environment defaults for test isolation
export PYTHONPATH="${SCRIPT_DIR}:${SCRIPT_DIR}/src"
export LANGSMITH_TRACING="false"
export WORKSPACE_ROOT="${SCRIPT_DIR}"

# Pytest execution with optional passthrough flags
echo "==> Executing test suite via uv..."
if [ "$#" -eq 0 ]; then
    uv run python -m pytest tests/ -v
else
    uv run python -m pytest "$@"
fi

TEST_EXIT_CODE=$?
if [ ${TEST_EXIT_CODE} -eq 0 ]; then
    echo "==> Test execution completed successfully."
else
    echo "==> Test execution failed with exit code ${TEST_EXIT_CODE}." >&2
fi

exit ${TEST_EXIT_CODE}