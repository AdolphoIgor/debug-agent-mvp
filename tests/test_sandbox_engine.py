from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.sandbox_engine import SandboxEngine, SandboxExecutionResult


def test_sandbox_execution_result_properties() -> None:
    success_res = SandboxExecutionResult(exit_code=0, stdout="OK", stderr="")
    assert success_res.passed is True

    fail_res = SandboxExecutionResult(exit_code=1, stdout="", stderr="Error")
    assert fail_res.passed is False


@patch("subprocess.run")
def test_clean_workspace_runs_git_commands(mock_run: MagicMock, mock_workspace: Path) -> None:
    engine = SandboxEngine(workspace_path=mock_workspace)
    engine.clean_workspace()

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        ["git", "reset", "--hard", "HEAD"],
        cwd=mock_workspace,
        capture_output=True,
        check=False,
    )
    mock_run.assert_any_call(
        ["git", "clean", "-fd"],
        cwd=mock_workspace,
        capture_output=True,
        check=False,
    )


def test_run_tests_rejects_missing_file(mock_workspace: Path) -> None:
    engine = SandboxEngine(workspace_path=mock_workspace)
    res = engine.run_tests("tests/non_existent.py")

    assert res.passed is False
    assert "Test file not found" in res.stderr


def test_run_tests_rejects_path_traversal(mock_workspace: Path) -> None:
    engine = SandboxEngine(workspace_path=mock_workspace)
    res = engine.run_tests("../../outside.py")

    assert res.passed is False
    assert "Test file not found" in res.stderr


@patch("subprocess.run")
def test_run_tests_patch_apply_failure(mock_run: MagicMock, mock_workspace: Path) -> None:
    test_file = mock_workspace / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")

    engine = SandboxEngine(workspace_path=mock_workspace)

    mock_run.side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Patch failed"),
    ]

    res = engine.run_tests("tests/test_sample.py", patch_content="corrupt diff")

    assert res.passed is False
    assert "Failed applying patch" in res.stderr


@patch("subprocess.run")
def test_run_tests_applies_patch_and_executes_docker(
    mock_run: MagicMock, mock_workspace: Path
) -> None:
    test_file = mock_workspace / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")

    engine = SandboxEngine(workspace_path=mock_workspace)

    mock_run.side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="1 passed", stderr=""),
    ]

    res = engine.run_tests("tests/test_sample.py", patch_content="diff --git ...")

    assert res.passed is True
    assert "1 passed" in res.stdout


@patch("subprocess.run")
def test_run_tests_run_full_suite(mock_run: MagicMock, mock_workspace: Path) -> None:
    test_file = mock_workspace / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")

    engine = SandboxEngine(workspace_path=mock_workspace)

    mock_run.side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="All passed", stderr=""),
    ]

    res = engine.run_tests("tests/test_sample.py", run_full_suite=True)

    assert res.passed is True
    docker_call = mock_run.call_args_list[-1][0][0]
    assert "tests" in docker_call


@patch("subprocess.run")
def test_run_tests_handles_timeout(mock_run: MagicMock, mock_workspace: Path) -> None:
    test_file = mock_workspace / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")

    engine = SandboxEngine(workspace_path=mock_workspace)
    mock_run.side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.TimeoutExpired(cmd="docker run", timeout=120),
    ]

    res = engine.run_tests("tests/test_sample.py")
    assert res.exit_code == -1
    assert "Sandbox execution timed out" in res.stderr


@patch("subprocess.run")
def test_run_tests_handles_generic_exception(mock_run: MagicMock, mock_workspace: Path) -> None:
    test_file = mock_workspace / "tests" / "test_sample.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")

    engine = SandboxEngine(workspace_path=mock_workspace)
    mock_run.side_effect = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        RuntimeError("Docker daemon connection failed"),
    ]

    res = engine.run_tests("tests/test_sample.py")
    assert res.exit_code == 1
    assert "Sandbox error: Docker daemon connection failed" in res.stderr
