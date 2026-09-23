from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator_graph import (
    WorkflowState,
    node_blind_auditor,
    node_publish_and_index,
    node_sandbox_execution,
    route_after_sandbox,
)


@pytest.fixture
def base_state() -> WorkflowState:
    return {
        "issue_id": "CORE-102",
        "problem_statement": "Prevent ZeroDivisionError when item count is zero",
        "current_phase": "reproduction",
        "is_test_locked": False,
        "locked_test_path": "",
        "locked_test_code": "",
        "candidate_test_path": "tests/test_reproduce_core_102.py",
        "candidate_test_code": "def test_zero_count():\n    assert False\n",
        "candidate_patch": "",
        "database_migration_artifacts": [],
        "audit_verdict": "PENDING",
        "auditor_critique": "",
        "programmer_feedback": "",
        "sandbox_passed": False,
        "sandbox_output": "",
        "stagnation_counter": 0,
        "consultant_cycles": 0,
        "consultant_guidance": "",
        "target_branch": "",
        "commit_message": "",
        "final_solution": "",
        "execution_status": "IN_PROGRESS",
    }


def test_sandbox_freezes_test_on_reproduction_failure(base_state, tmp_path, monkeypatch):
    monkeypatch.setattr("src.orchestrator_graph.WORKSPACE_ROOT", tmp_path)

    with patch.object(
        type("MockSandbox", (), {}), "run_hermetic_pytest", return_value=(1, "FAILED (failures=1)")
    ):
        with patch("src.orchestrator_graph.PythonHermeticSandbox.run_hermetic_pytest") as mock_exec:
            mock_exec.return_value = (1, "FAILED tests/test_reproduce_core_102.py - AssertionError")

            updates = node_sandbox_execution(base_state)

            assert updates["sandbox_passed"] is True
            assert updates["is_test_locked"] is True
            assert updates["current_phase"] == "resolution"
            assert updates["locked_test_path"] == base_state["candidate_test_path"]
            assert updates["stagnation_counter"] == 0
            assert updates["consultant_cycles"] == 0


def test_auditor_rejects_patch_modifying_locked_test(base_state):
    base_state["current_phase"] = "resolution"
    base_state["is_test_locked"] = True
    base_state["locked_test_path"] = "tests/test_locked.py"
    base_state["candidate_patch"] = (
        "--- a/tests/test_locked.py\n+++ b/tests/test_locked.py\n@@ -1,1 +1,1 @@\n-assert False\n+assert True\n"
    )

    mock_response = MagicMock()
    mock_response.text = '{"audit_verdict": "APPROVE", "critique": "Looks fine"}'

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_response

        updates = node_blind_auditor(base_state)

        # Enforce that auditor strictly overrides verdict if locked test file is modified
        assert updates["audit_verdict"] == "REJECT"
        assert "Security violation" in updates["auditor_critique"]
        assert updates["stagnation_counter"] == 1


def test_publish_creates_timestamped_branch(base_state, tmp_path, monkeypatch):
    monkeypatch.setattr("src.orchestrator_graph.WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "resolution"
    base_state["is_test_locked"] = True
    base_state["locked_test_path"] = "tests/test_frozen.py"
    base_state["locked_test_code"] = "def test_ok(): pass\n"
    base_state["candidate_patch"] = ""
    base_state["database_migration_artifacts"] = ["migrations/001_initial.sql"]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        updates = node_publish_and_index(base_state)

        assert updates["execution_status"] == "SUCCESS"
        assert updates["target_branch"].startswith("fix/issue-core-102-")
        assert "Database migration artifacts" in updates["commit_message"]


def test_routing_reproduction_phase():
    state: WorkflowState = {
        "current_phase": "reproduction",
        "sandbox_passed": True,
        "stagnation_counter": 0,
        "consultant_cycles": 0,
    }  # type: ignore

    # When reproduction succeeds, sandbox routes back to programmer to initiate phase 2
    assert route_after_sandbox(state) == "node_programmer"


def test_routing_resolution_phase_success():
    state: WorkflowState = {
        "current_phase": "resolution",
        "sandbox_passed": True,
        "stagnation_counter": 0,
        "consultant_cycles": 0,
    }  # type: ignore

    # When resolution succeeds, sandbox routes straight to autonomous publishing
    assert route_after_sandbox(state) == "node_publish_and_index"
