from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors

from orchestrator_graph import (
    AntagonistAudit,
    AssistantPatch,
    ExecutionLockManager,
    OrchestratorState,
    build_mvp_showcase_graph,
    node_blind_auditor,
    node_sandbox_execution,
    route_after_audit,
    route_after_consultant,
    route_after_sandbox,
)


@patch("fcntl.flock")
def test_execution_lock_manager_lifecycle(
    mock_flock: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_lock_file = tmp_path / "test_exec.lock"
    monkeypatch.setattr("orchestrator_graph.LOCK_FILE_PATH", test_lock_file)

    # Simulate the sequential calls to fcntl.flock to trigger the contention failure on the second acquire
    mock_flock.side_effect = [
        None,  # 1st acquire (success)
        BlockingIOError("Locked"),  # 2nd acquire (fails simulating contention)
        None,  # release
        None,  # 3rd acquire (success)
        None,  # final release
    ]

    ExecutionLockManager.release()
    ExecutionLockManager.acquire()
    assert test_lock_file.exists()

    with pytest.raises(
        RuntimeError, match="Another active orchestration process holds the global system lock"
    ):
        ExecutionLockManager.acquire()

    ExecutionLockManager.release()
    ExecutionLockManager.acquire()
    ExecutionLockManager.release()


def test_assistant_patch_schema_validation() -> None:
    valid_payload = {
        "analysis": "Identified zero division bug.",
        "unified_diff": "--- a/math.py\n+++ b/math.py\n@@ -1 +1 @@\n-return 1/x\n+return 1/x if x != 0 else 0",
        "unit_test_rel_path": "tests/test_math.py",
        "unit_test_code": "def test_zero_div(): assert True",
    }
    patch_obj = AssistantPatch.model_validate(valid_payload)
    assert patch_obj.unit_test_rel_path == "tests/test_math.py"
    assert "zero division" in patch_obj.analysis


def test_route_after_audit_transitions() -> None:
    approved_state: OrchestratorState = {
        "last_audit": AntagonistAudit(verdict="APPROVE", critique="Valid logic."),
        "stagnation_counter": 0,
    }  # type: ignore[typeddict-item]
    assert route_after_audit(approved_state) == "node_sandbox_execution"

    rejected_retry_state: OrchestratorState = {
        "last_audit": AntagonistAudit(verdict="REJECT", critique="Missing mocks."),
        "stagnation_counter": 1,
    }  # type: ignore[typeddict-item]
    assert route_after_audit(rejected_retry_state) == "node_programmer"

    rejected_stagnated_state: OrchestratorState = {
        "last_audit": AntagonistAudit(verdict="REJECT", critique="Repeated failure."),
        "stagnation_counter": 3,
    }  # type: ignore[typeddict-item]
    assert route_after_audit(rejected_stagnated_state) == "node_consultant"


def test_route_after_consultant_transitions() -> None:
    active_cycle_state: OrchestratorState = {"consultant_cycle_counter": 1}  # type: ignore[typeddict-item]
    assert route_after_consultant(active_cycle_state) == "node_programmer"

    exhausted_cycle_state: OrchestratorState = {"consultant_cycle_counter": 3}  # type: ignore[typeddict-item]
    assert route_after_consultant(exhausted_cycle_state) == "node_archive_failure"


def test_route_after_sandbox_transitions() -> None:
    passed_state: OrchestratorState = {"tests_passed": True}  # type: ignore[typeddict-item]
    assert route_after_sandbox(passed_state) == "node_publish_and_index"

    failed_retry_state: OrchestratorState = {
        "tests_passed": False,
        "stagnation_counter": 1,
    }  # type: ignore[typeddict-item]
    assert route_after_sandbox(failed_retry_state) == "node_programmer"

    failed_stagnated_state: OrchestratorState = {
        "tests_passed": False,
        "stagnation_counter": 3,
    }  # type: ignore[typeddict-item]
    assert route_after_sandbox(failed_stagnated_state) == "node_consultant"


@patch("orchestrator_graph.get_quota_pool")
def test_node_blind_auditor_reports_quota_exhaustion_on_429(mock_get_pool: MagicMock) -> None:
    mock_pool = MagicMock()
    mock_pool.get_active_model.return_value = "gemini-1.5-pro"
    mock_client = MagicMock()

    # APIError requires both a message and a response argument
    api_error = errors.APIError("Rate limit reached", MagicMock())
    api_error.code = 429
    mock_client.models.generate_content.side_effect = api_error

    mock_pool.client = mock_client
    mock_get_pool.return_value = mock_pool

    state: OrchestratorState = {
        "current_patch": AssistantPatch(
            analysis="test",
            unified_diff="",
            unit_test_rel_path="test_a.py",
            unit_test_code="",
        ),
        "stagnation_counter": 0,
    }  # type: ignore[typeddict-item]

    with pytest.raises(errors.APIError):
        node_blind_auditor(state)

    mock_pool.report_exhaustion.assert_called_once_with("gemini-1.5-pro")


@patch("orchestrator_graph.PythonHermeticSandbox")
@patch("subprocess.run")
def test_node_sandbox_execution_success(
    mock_sub_run: MagicMock,
    mock_sandbox_cls: MagicMock,
    mock_workspace: Path,
) -> None:
    mock_sandbox_instance = MagicMock()
    mock_sandbox_instance.apply_patch.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr=""
    )
    mock_sandbox_instance.run_pytest.return_value = MagicMock(
        returncode=0, stdout="OK", stderr="", timed_out=False
    )
    mock_sandbox_cls.return_value = mock_sandbox_instance

    state: OrchestratorState = {
        "ticket_id": "T-100",
        "workspace_path": str(mock_workspace),
        "current_patch": AssistantPatch(
            analysis="Analysis",
            unified_diff="--- a\n+++ b",
            unit_test_rel_path="tests/test_fix.py",
            unit_test_code="def test_fix(): pass",
        ),
        "stagnation_counter": 0,
    }  # type: ignore[typeddict-item]

    result = node_sandbox_execution(state)

    assert result["tests_passed"] is True
    assert result["stagnation_counter"] == 0
    mock_sandbox_instance.write_test_file_securely.assert_called_once_with(
        "tests/test_fix.py", "def test_fix(): pass"
    )


def test_build_mvp_showcase_graph_compilation() -> None:
    graph = build_mvp_showcase_graph()
    app = graph.compile()
    assert app is not None
    assert "node_acquire_execution_lock" in app.nodes
    assert "node_publish_and_index" in app.nodes
    assert "node_archive_failure" in app.nodes
