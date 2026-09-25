import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END

import src.orchestrator_graph as og
from src.orchestrator_graph import (
    WorkflowState,
    _execute_mcp_tool_call,
    _get_mcp_tools_for_llm,
    build_orchestrator_graph,
    node_blind_auditor,
    node_consultant,
    node_git_sync,
    node_programmer,
    node_publish_and_index,
    node_sandbox_execution,
    route_after_audit,
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


def test_node_git_sync_with_delta(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    with (
        patch("subprocess.run") as mock_run,
        patch("src.orchestrator_graph.PythonStructuralIndexer") as mock_indexer_cls,
    ):
        mock_run.return_value = MagicMock(stdout="src/service.py\nREADME.md\n")
        indexer_instance = MagicMock()
        mock_indexer_cls.return_value = indexer_instance

        updates = node_git_sync(base_state)

        indexer_instance.sync_project_files.assert_called_once_with(
            files_to_sync=["src/service.py"]
        )
        assert updates["execution_status"] == "IN_PROGRESS"
        assert updates["current_phase"] == "reproduction"


def test_node_git_sync_no_delta(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    with (
        patch("subprocess.run") as mock_run,
        patch("src.orchestrator_graph.PythonStructuralIndexer") as mock_indexer_cls,
    ):
        mock_run.return_value = MagicMock(stdout="")
        indexer_instance = MagicMock()
        mock_indexer_cls.return_value = indexer_instance

        updates = node_git_sync(base_state)

        indexer_instance.sync_project_files.assert_not_called()
        assert updates["execution_status"] == "IN_PROGRESS"


def test_node_programmer_reproduction_phase(base_state: WorkflowState) -> None:
    base_state["current_phase"] = "reproduction"
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "candidate_test_path": "tests/test_reproduce_core_102.py",
            "candidate_test_code": "def test_reproduce(): assert False",
        }
    )

    with (
        patch("google.genai.Client") as mock_client_cls,
        patch("src.orchestrator_graph._get_mcp_tools_for_llm", return_value=[]),
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_resp

        updates = node_programmer(base_state)

        assert updates["candidate_test_path"] == "tests/test_reproduce_core_102.py"
        assert "def test_reproduce" in updates["candidate_test_code"]
        assert updates["candidate_patch"] == ""


def test_node_programmer_resolution_phase(base_state: WorkflowState) -> None:
    base_state["current_phase"] = "resolution"
    base_state["locked_test_path"] = "tests/test_frozen.py"
    base_state["locked_test_code"] = "def test_frozen(): assert True"
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {
            "candidate_patch": "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1 +1 @@\n-0\n+1\n",
            "database_migration_artifacts": ["migrations/002_fix.sql"],
        }
    )

    with (
        patch("google.genai.Client") as mock_client_cls,
        patch("src.orchestrator_graph._get_mcp_tools_for_llm", return_value=[]),
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_resp

        updates = node_programmer(base_state)

        assert "--- a/src/calc.py" in updates["candidate_patch"]
        assert updates["database_migration_artifacts"] == ["migrations/002_fix.sql"]


def test_node_blind_auditor_reproduction_phase(base_state: WorkflowState) -> None:
    base_state["current_phase"] = "reproduction"
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(
        {"audit_verdict": "APPROVE", "critique": "Solid reproduction test."}
    )

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_resp

        updates = node_blind_auditor(base_state)
        assert updates["audit_verdict"] == "APPROVE"
        assert updates["auditor_critique"] == "Solid reproduction test."


def test_node_blind_auditor_rejects_patch_modifying_locked_test(base_state: WorkflowState) -> None:
    base_state["current_phase"] = "resolution"
    base_state["is_test_locked"] = True
    base_state["locked_test_path"] = "tests/test_locked.py"
    base_state["candidate_patch"] = (
        "--- a/tests/test_locked.py\n+++ b/tests/test_locked.py\n@@ -1,1 +1,1 @@\n-assert False\n+assert True\n"
    )
    mock_resp = MagicMock()
    mock_resp.text = '{"audit_verdict": "APPROVE", "critique": "Looks fine"}'

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_resp

        updates = node_blind_auditor(base_state)

        assert updates["audit_verdict"] == "REJECT"
        assert "Security violation" in updates["auditor_critique"]
        assert updates["stagnation_counter"] == 1


def test_node_blind_auditor_json_fallback(base_state: WorkflowState) -> None:
    base_state["current_phase"] = "reproduction"
    mock_resp = MagicMock()
    mock_resp.text = (
        'Raw preamble: {"audit_verdict": "REJECT", "critique": "Lacks mocks"} trailing text'
    )

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_resp

        updates = node_blind_auditor(base_state)
        assert updates["audit_verdict"] == "REJECT"
        assert updates["auditor_critique"] == "Lacks mocks"


def test_node_sandbox_execution_freezes_test_on_reproduction(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "reproduction"

    with patch("src.orchestrator_graph.PythonHermeticSandbox.run_hermetic_pytest") as mock_exec:
        mock_exec.return_value = (1, "FAILED tests/test_reproduce_core_102.py - AssertionError")

        updates = node_sandbox_execution(base_state)

        assert updates["sandbox_passed"] is True
        assert updates["is_test_locked"] is True
        assert updates["current_phase"] == "resolution"
        assert updates["locked_test_path"] == base_state["candidate_test_path"]
        assert updates["stagnation_counter"] == 0


def test_node_sandbox_execution_reproduction_syntax_error(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "reproduction"

    with patch("src.orchestrator_graph.PythonHermeticSandbox.run_hermetic_pytest") as mock_exec:
        mock_exec.return_value = (1, "SyntaxError: invalid syntax")

        updates = node_sandbox_execution(base_state)

        assert updates["sandbox_passed"] is False
        assert updates["stagnation_counter"] == 1
        assert "SyntaxError" in updates["programmer_feedback"]


def test_node_sandbox_execution_reproduction_passes_without_reproducing(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "reproduction"

    with patch("src.orchestrator_graph.PythonHermeticSandbox.run_hermetic_pytest") as mock_exec:
        mock_exec.return_value = (0, "1 passed")

        updates = node_sandbox_execution(base_state)
        assert updates["sandbox_passed"] is False
        assert updates["stagnation_counter"] == 1
        assert "Test passed on baseline code" in updates["programmer_feedback"]


def test_node_sandbox_execution_resolution_success(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "resolution"
    base_state["locked_test_path"] = "tests/test_locked.py"
    base_state["locked_test_code"] = "def test_ok(): pass"

    with patch("src.orchestrator_graph.PythonHermeticSandbox.run_hermetic_pytest") as mock_exec:
        mock_exec.return_value = (0, "1 passed in 0.05s")

        updates = node_sandbox_execution(base_state)

        assert updates["sandbox_passed"] is True
        assert updates["stagnation_counter"] == 0


def test_node_sandbox_execution_resolution_failure(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "resolution"
    base_state["locked_test_path"] = "tests/test_locked.py"
    base_state["locked_test_code"] = "def test_ok(): pass"

    with patch("src.orchestrator_graph.PythonHermeticSandbox.run_hermetic_pytest") as mock_exec:
        mock_exec.return_value = (1, "FAILED test_locked.py - AssertionError")

        updates = node_sandbox_execution(base_state)

        assert updates["sandbox_passed"] is False
        assert updates["stagnation_counter"] == 1
        assert "Tests failed under applied patch" in updates["programmer_feedback"]


def test_node_consultant_resets_stagnation(base_state: WorkflowState) -> None:
    base_state["stagnation_counter"] = 3
    base_state["consultant_cycles"] = 0
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({"consultant_guidance": "Refactor the module dependency."})

    with (
        patch("google.genai.Client") as mock_client_cls,
        patch("src.orchestrator_graph._get_mcp_tools_for_llm", return_value=[]),
    ):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.models.generate_content.return_value = mock_resp

        updates = node_consultant(base_state)

        assert updates["consultant_guidance"] == "Refactor the module dependency."
        assert updates["stagnation_counter"] == 0
        assert updates["consultant_cycles"] == 1


def test_publish_creates_timestamped_branch(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "resolution"
    base_state["is_test_locked"] = True
    base_state["locked_test_path"] = "tests/test_frozen.py"
    base_state["locked_test_code"] = "def test_ok(): pass\n"
    base_state["candidate_patch"] = "--- a/file.py\n+++ b/file.py\n"
    base_state["database_migration_artifacts"] = ["migrations/001_initial.sql"]

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        updates = node_publish_and_index(base_state)

        assert updates["execution_status"] == "SUCCESS"
        assert updates["target_branch"].startswith("fix/issue-core-102-")
        assert "Database migration artifacts" in updates["commit_message"]


def test_publish_handles_git_push_nonzero(
    base_state: WorkflowState, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    base_state["current_phase"] = "resolution"
    base_state["locked_test_path"] = "tests/test_frozen.py"
    base_state["locked_test_code"] = "def test_ok(): pass\n"

    def mock_run_side_effect(cmd, *args, **kwargs):
        if len(cmd) > 1 and cmd[1] == "push":
            return MagicMock(returncode=1, stderr="Remote rejected")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=mock_run_side_effect):
        updates = node_publish_and_index(base_state)
        assert updates["execution_status"] == "SUCCESS"


def test_route_after_audit_branches() -> None:
    approved: WorkflowState = {"audit_verdict": "APPROVE"}  # type: ignore
    assert route_after_audit(approved) == "node_sandbox_execution"

    retry: WorkflowState = {
        "audit_verdict": "REJECT",
        "stagnation_counter": 1,
        "consultant_cycles": 0,
    }  # type: ignore
    assert route_after_audit(retry) == "node_programmer"

    escalate: WorkflowState = {
        "audit_verdict": "REJECT",
        "stagnation_counter": 3,
        "consultant_cycles": 0,
    }  # type: ignore
    assert route_after_audit(escalate) == "node_consultant"

    terminate: WorkflowState = {
        "audit_verdict": "REJECT",
        "stagnation_counter": 3,
        "consultant_cycles": 2,
    }  # type: ignore
    assert route_after_audit(terminate) == END


def test_route_after_sandbox_branches() -> None:
    repro_success: WorkflowState = {"current_phase": "reproduction", "sandbox_passed": True}  # type: ignore
    assert route_after_sandbox(repro_success) == "node_programmer"

    res_success: WorkflowState = {"current_phase": "resolution", "sandbox_passed": True}  # type: ignore
    assert route_after_sandbox(res_success) == "node_publish_and_index"

    stagnated: WorkflowState = {  # type: ignore
        "current_phase": "resolution",
        "sandbox_passed": False,
        "stagnation_counter": 3,
        "consultant_cycles": 1,
    }
    assert route_after_sandbox(stagnated) == "node_consultant"

    max_cycles: WorkflowState = {  # type: ignore
        "current_phase": "reproduction",
        "sandbox_passed": False,
        "stagnation_counter": 3,
        "consultant_cycles": 2,
    }
    assert route_after_sandbox(max_cycles) == END


@pytest.mark.asyncio
async def test_execute_mcp_tool_call_sse_success() -> None:
    mock_session = AsyncMock()
    mock_session.call_tool.return_value = MagicMock(content="sse_tool_result")

    mock_sse = MagicMock()
    mock_sse.__aenter__.return_value = (MagicMock(), MagicMock())
    mock_sse.__aexit__.return_value = None

    with (
        patch("src.orchestrator_graph.sse_client", return_value=mock_sse),
        patch("src.orchestrator_graph.ClientSession") as mock_session_cls,
    ):
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_session_cls.return_value.__aexit__.return_value = None

        res = await _execute_mcp_tool_call("any_tool", {"arg": "val"})
        assert res == "sse_tool_result"


@pytest.mark.asyncio
async def test_execute_mcp_tool_call_fallback_all_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(og, "WORKSPACE_ROOT", tmp_path)
    sample_file = tmp_path / "module.py"
    sample_file.write_text("def run():\n    return 42\n", encoding="utf-8")

    with (
        patch("src.orchestrator_graph.sse_client", side_effect=ConnectionRefusedError("Offline")),
        patch("src.orchestrator_graph.PythonStructuralIndexer") as mock_indexer_cls,
    ):
        indexer_instance = MagicMock()
        indexer_instance.query_semantic_sources.return_value = {"status": "search_ok"}
        indexer_instance.get_symbol_blast_radius.return_value = {"status": "blast_ok"}
        mock_indexer_cls.return_value = indexer_instance

        res_search = await _execute_mcp_tool_call("search_codebase", {"issue_description": "test"})
        assert res_search == {"status": "search_ok"}

        res_blast = await _execute_mcp_tool_call("get_symbol_blast_radius", {"symbol_name": "run"})
        assert res_blast == {"status": "blast_ok"}

        res_read = await _execute_mcp_tool_call(
            "read_source_file", {"file_path": "module.py", "start_line": 1, "end_line": 2}
        )
        assert "def run():" in res_read

        res_invalid_path = await _execute_mcp_tool_call(
            "read_source_file", {"file_path": "absent.py"}
        )
        assert "[Error: Invalid file path" in res_invalid_path

        res_unknown = await _execute_mcp_tool_call("unknown_tool", {})
        assert "[Error: Unknown tool" in res_unknown


def test_get_mcp_tools_for_llm_execution() -> None:
    with patch(
        "src.orchestrator_graph._execute_mcp_tool_call", new_callable=AsyncMock
    ) as mock_exec:
        mock_exec.return_value = {"status": "ok"}
        tools = _get_mcp_tools_for_llm()
        search_tool, blast_tool, read_tool = tools

        res_search = search_tool("bug in parser")
        assert "status" in res_search

        res_blast = blast_tool("calculate_tax")
        assert "status" in res_blast

        mock_exec.return_value = "file content output"
        res_read = read_tool("app.py", 1, 10)
        assert res_read == "file content output"


def test_build_orchestrator_graph_compiles() -> None:
    graph = build_orchestrator_graph()
    assert graph is not None
