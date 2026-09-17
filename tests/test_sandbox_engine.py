from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sandbox_engine import PythonHermeticSandbox, SandboxExecutionResult


def test_write_test_file_securely_success(mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(workspace_path=mock_workspace, ticket_id="TCK-101")
    target_rel_path = "tests/test_generated.py"
    content = "def test_success():\n    assert 1 == 1\n"

    sandbox.write_test_file_securely(target_rel_path, content)

    created_file = mock_workspace / target_rel_path
    assert created_file.exists()
    assert created_file.read_text(encoding="utf-8") == content


def test_write_test_file_securely_rejects_path_traversal(mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(workspace_path=mock_workspace, ticket_id="TCK-102")
    traversal_path = "../../escaped_test.py"

    with pytest.raises(PermissionError, match="Path traversal attempt detected"):
        sandbox.write_test_file_securely(traversal_path, "assert True")


def test_write_test_file_securely_rejects_git_directory_target(mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(workspace_path=mock_workspace, ticket_id="TCK-103")
    forbidden_target = ".git/hooks/pre-commit"

    with pytest.raises(PermissionError, match="Modifications to the .git directory are prohibited"):
        sandbox.write_test_file_securely(forbidden_target, "#!/bin/sh\nexit 1")


@patch("subprocess.run")
def test_apply_patch_invokes_git_apply(mock_run: MagicMock, mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(workspace_path=mock_workspace, ticket_id="TCK-104")
    patch_diff = (
        "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-def run():\n+def run():\n+    return False"
    )

    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    result = sandbox.apply_patch(patch_diff)

    assert result.returncode == 0
    mock_run.assert_called_once_with(
        ["git", "apply", "--whitespace=fix", "-"],
        input=patch_diff,
        text=True,
        cwd=str(mock_workspace),
        capture_output=True,
        check=False,
    )


@patch("subprocess.run")
def test_run_pytest_executes_hermetic_container(mock_run: MagicMock, mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(
        workspace_path=mock_workspace,
        ticket_id="TCK-105",
        timeout_sec=30,
    )

    mock_run.return_value = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="1 passed in 0.02s",
        stderr="",
    )

    result: SandboxExecutionResult = sandbox.run_pytest(test_file="tests/test_patch.py")

    assert result.returncode == 0
    assert result.timed_out is False
    assert "1 passed" in result.stdout

    expected_cmd = [
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "-v",
        f"{mock_workspace}:/workspace:rw",
        "-w",
        "/workspace",
        "mvp-sandbox:test",
        "pytest",
        "-q",
        "tests/test_patch.py",
    ]
    mock_run.assert_called_once_with(
        expected_cmd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@patch("subprocess.run")
def test_run_pytest_handles_timeout(mock_run: MagicMock, mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(workspace_path=mock_workspace, ticket_id="TCK-106")
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd="docker run", timeout=15, output=b"Running..."
    )
    result = sandbox.run_pytest()
    assert result.returncode == -1
    assert result.timed_out is True
    assert "exceeded configured timeout" in result.stderr


@patch("subprocess.run")
def test_run_pytest_handles_os_error(mock_run: MagicMock, mock_workspace: Path) -> None:
    sandbox = PythonHermeticSandbox(workspace_path=mock_workspace, ticket_id="TCK-107")
    mock_run.side_effect = FileNotFoundError("Docker executable not found on host path.")

    result = sandbox.run_pytest()

    assert result.returncode == -1
    assert result.timed_out is False
    assert "Container execution failure" in result.stderr
