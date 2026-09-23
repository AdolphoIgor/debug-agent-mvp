import json
import logging
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from langgraph.graph import END, START, StateGraph
from mcp import ClientSession
from mcp.client.sse import sse_client
from typing_extensions import TypedDict

from src.code_indexer import PythonStructuralIndexer

load_dotenv()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace")).resolve()
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "mvp-sandbox:latest")
SANDBOX_TIMEOUT_SEC = int(os.getenv("SANDBOX_TIMEOUT_SEC", "120"))
MCP_SERVER_SSE_URL = os.getenv("MCP_SERVER_SSE_URL", "http://127.0.0.1:8080/sse")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

STAGNATION_THRESHOLD = 3
MAX_CONSULTANT_CYCLES = 2


class WorkflowState(TypedDict):
    issue_id: str
    problem_statement: str
    current_phase: Literal["reproduction", "resolution"]
    is_test_locked: bool
    locked_test_path: str
    locked_test_code: str
    candidate_test_path: str
    candidate_test_code: str
    candidate_patch: str
    database_migration_artifacts: list[str]
    audit_verdict: Literal["APPROVE", "REJECT", "PENDING"]
    auditor_critique: str
    programmer_feedback: str
    sandbox_passed: bool
    sandbox_output: str
    stagnation_counter: int
    consultant_cycles: int
    consultant_guidance: str
    target_branch: str
    commit_message: str
    final_solution: str
    execution_status: Literal["IN_PROGRESS", "SUCCESS", "FAILED", "ESCALATED"]


async def _execute_mcp_tool_call(tool_name: str, arguments: dict[str, Any]) -> Any:
    """
    Connects to the Code Intelligence FastMCP server via SSE to execute remote tools.
    Falls back to direct indexer invocation if the sidecar server is unreachable.
    """
    try:
        async with sse_client(MCP_SERVER_SSE_URL) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.call_tool(tool_name, arguments)
                return response.content
    except Exception as exc:
        logger.warning(
            "MCP sidecar unreachable at %s (%s). Executing local fallback.",
            MCP_SERVER_SSE_URL,
            exc,
        )
        indexer = PythonStructuralIndexer(
            repo_path=str(WORKSPACE_ROOT),
            postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
            postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
            postgres_db=os.getenv("POSTGRES_DB", "mvp_db"),
            postgres_user=os.getenv("POSTGRES_USER", "mvp_user"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", "mvp_password"),
            qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333"),
        )
        if tool_name == "search_codebase":
            return indexer.query_semantic_sources(
                issue_description=arguments.get("issue_description", ""),
                top_k=arguments.get("top_k", 5),
                max_caller_depth=arguments.get("max_caller_depth", 2),
            )
        elif tool_name == "get_symbol_blast_radius":
            return indexer.get_symbol_blast_radius(
                symbol_name=arguments.get("symbol_name", ""),
                max_depth=arguments.get("max_caller_depth", 3),
            )
        elif tool_name == "read_source_file":
            target = (WORKSPACE_ROOT / arguments.get("file_path", "")).resolve()
            if not target.is_file() or not target.is_relative_to(WORKSPACE_ROOT):
                return f"[Error: Invalid file path {arguments.get('file_path')}]"
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(1, arguments.get("start_line", 1))
            end = (
                len(lines) if arguments.get("end_line", -1) == -1 else arguments.get("end_line", -1)
            )
            selected = lines[start - 1 : end]
            return "\n".join(f"{idx:4d} | {line}" for idx, line in enumerate(selected, start=start))
        return f"[Error: Unknown tool {tool_name}]"


class PythonHermeticSandbox:
    """
    Executes tests and patches within an isolated Docker container with
    zero network connectivity, unprivileged user permissions, and dropped capabilities.
    """

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path

    def clean_workspace(self) -> None:
        """
        Resets working tree state to discard uncommitted artifacts.
        """
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=self.workspace_path,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=self.workspace_path,
            capture_output=True,
            check=False,
        )

    def run_hermetic_pytest(
        self,
        test_path: str,
        patch_content: str | None = None,
        run_full_suite: bool = False,
    ) -> tuple[int, str]:
        """
        Runs pytest inside the hermetic container and returns exit code and output.
        """
        self.clean_workspace()

        if patch_content and patch_content.strip():
            patch_file = self.workspace_path / ".temp_exec.patch"
            try:
                patch_file.write_text(patch_content, encoding="utf-8")
                apply_res = subprocess.run(
                    ["git", "apply", "--whitespace=nowarn", str(patch_file)],
                    cwd=self.workspace_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if apply_res.returncode != 0:
                    return 1, f"Failed applying unified diff patch:\n{apply_res.stderr}"
            finally:
                if patch_file.exists():
                    patch_file.unlink()

        target_test_file = (self.workspace_path / test_path).resolve()
        if not target_test_file.is_file() or not target_test_file.is_relative_to(
            self.workspace_path
        ):
            return 1, f"Regression test file does not exist at: {test_path}"

        test_rel_path = str(target_test_file.relative_to(self.workspace_path))
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-u",
            "1000:1000",
            "-v",
            f"{self.workspace_path}:/workspace",
            "-w",
            "/workspace",
            "-e",
            "PYTHONPATH=/workspace:/workspace/src",
            SANDBOX_IMAGE,
            "pytest",
            "-q",
        ]

        if run_full_suite:
            cmd.extend([test_rel_path, "tests"])
        else:
            cmd.append(test_rel_path)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SANDBOX_TIMEOUT_SEC,
                check=False,
            )
            output = f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
            return res.returncode, output
        except subprocess.TimeoutExpired:
            return -1, f"Sandbox test execution timed out after {SANDBOX_TIMEOUT_SEC} seconds."
        except Exception as exc:
            return 1, f"Sandbox invocation failure: {exc}"


def node_git_sync(state: WorkflowState) -> dict[str, Any]:
    """
    Synchronizes repository state and deterministically triggers reindexing
    only when a Git delta is detected.
    """
    logger.info("Executing Git synchronization and delta verification.")
    diff_cmd = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    changed_files = [f.strip() for f in diff_cmd.stdout.splitlines() if f.strip().endswith(".py")]

    indexer = PythonStructuralIndexer(
        repo_path=str(WORKSPACE_ROOT),
        postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "mvp_db"),
        postgres_user=os.getenv("POSTGRES_USER", "mvp_user"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "mvp_password"),
        qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333"),
    )

    if changed_files:
        logger.info("Delta detected in %d files. Executing structural sync.", len(changed_files))
        indexer.sync_project_files(files_to_sync=changed_files)
    else:
        logger.info("No delta detected. Codebase index is up to date.")

    return {
        "execution_status": "IN_PROGRESS",
        "stagnation_counter": 0,
        "consultant_cycles": 0,
    }


def _get_mcp_tools_for_llm():
    """
    Defines tools callable by Gemini that proxy to the Code Intelligence MCP server.
    """

    def search_codebase(issue_description: str, top_k: int = 5, max_caller_depth: int = 2) -> str:
        """Search codebase semantically using vector index and call graph blast radius."""
        import asyncio

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            _execute_mcp_tool_call(
                "search_codebase",
                {
                    "issue_description": issue_description,
                    "top_k": top_k,
                    "max_caller_depth": max_caller_depth,
                },
            )
        )
        loop.close()
        return json.dumps(res, default=str)

    def get_symbol_blast_radius(symbol_name: str, max_caller_depth: int = 3) -> str:
        """Retrieve calling hierarchy and dependent callers for a function or class."""
        import asyncio

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            _execute_mcp_tool_call(
                "get_symbol_blast_radius",
                {"symbol_name": symbol_name, "max_caller_depth": max_caller_depth},
            )
        )
        loop.close()
        return json.dumps(res, default=str)

    def read_source_file(file_path: str, start_line: int = 1, end_line: int = -1) -> str:
        """Read source code content within the repository safely."""
        import asyncio

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(
            _execute_mcp_tool_call(
                "read_source_file",
                {"file_path": file_path, "start_line": start_line, "end_line": end_line},
            )
        )
        loop.close()
        return str(res)

    return [search_codebase, get_symbol_blast_radius, read_source_file]


def node_programmer(state: WorkflowState) -> dict[str, Any]:
    """
    Programmer node executing in two distinct phases:
    Phase 1: Reproduction - strictly author isolated regression tests using mocks.
    Phase 2: Resolution - author code diff patches and DDL migration scripts while test remains locked.
    """
    client = genai.Client()
    phase = state["current_phase"]
    tools = _get_mcp_tools_for_llm()

    if phase == "reproduction":
        system_instruction = (
            "You are an expert autonomous software engineer specializing in bug reproduction.\n"
            "PHASE 1: REPRODUCTION.\n"
            "Your SOLE objective is to write a pytest regression test using in-memory mocks (unittest.mock/pytest-mock)\n"
            "that captures the reported problem and FAILS on the current codebase, demonstrating reproduction.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Use tools to search the codebase and read relevant source files.\n"
            "2. DO NOT modify any production source code or produce any git diff patch.\n"
            "3. Your regression test must run in a hermetic environment with --network=none.\n"
            "4. Return a JSON object with: candidate_test_path (e.g. 'tests/test_reproduce_issue.py') "
            "and candidate_test_code (complete executable python code)."
        )
        user_prompt = (
            f"Issue ID: {state['issue_id']}\n"
            f"Problem Statement: {state['problem_statement']}\n"
            f"Programmer Feedback from previous attempt: {state.get('programmer_feedback', 'None')}\n"
            f"Consultant Guidance: {state.get('consultant_guidance', 'None')}\n\n"
            "Generate the reproducing regression test with in-memory mocks now."
        )
    else:
        system_instruction = (
            "You are an expert autonomous software engineer resolving software defects.\n"
            "PHASE 2: RESOLUTION.\n"
            "The regression test is FROZEN AND IMMUTABLE. You CANNOT modify the test.\n"
            "Your objective is to fix the production code and author any necessary database schema changes.\n"
            "CRITICAL INSTRUCTIONS:\n"
            "1. Inspect the locked regression test and navigate codebase using tools.\n"
            "2. Generate candidate_patch in standard Git unified diff format modifying only production files "
            "or new/modified migration scripts (e.g. migrations/*.sql or alembic).\n"
            "3. If any database changes are required, stage them as repository migration artifacts and list them "
            "in database_migration_artifacts.\n"
            "4. Return a JSON object with: candidate_patch (string), database_migration_artifacts (list of file paths), "
            "and explanation (string)."
        )
        user_prompt = (
            f"Issue ID: {state['issue_id']}\n"
            f"Problem Statement: {state['problem_statement']}\n"
            f"Locked Regression Test Path: {state['locked_test_path']}\n"
            f"Locked Regression Test Code:\n{state['locked_test_code']}\n\n"
            f"Sandbox Output from previous attempt:\n{state.get('sandbox_output', 'None')}\n"
            f"Auditor Feedback:\n{state.get('programmer_feedback', 'None')}\n"
            f"Consultant Guidance:\n{state.get('consultant_guidance', 'None')}\n\n"
            "Generate the production patch and migration artifacts to resolve the defect."
        )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text or ""
    try:
        parsed = json.loads(raw_text)
    except Exception:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {}

    updates: dict[str, Any] = {"audit_verdict": "PENDING"}

    if phase == "reproduction":
        test_path = parsed.get(
            "candidate_test_path", f"tests/test_reproduce_{state['issue_id']}.py"
        )
        test_code = parsed.get("candidate_test_code", "")
        updates["candidate_test_path"] = test_path
        updates["candidate_test_code"] = test_code
        updates["candidate_patch"] = ""
    else:
        updates["candidate_patch"] = parsed.get("candidate_patch", "")
        updates["database_migration_artifacts"] = parsed.get("database_migration_artifacts", [])

    return updates


def node_blind_auditor(state: WorkflowState) -> dict[str, Any]:
    """
    Evaluates proposed changes in zero-context mode.
    Phase 1: Validates that regression test faithfully models the problem with valid mocks.
    Phase 2: Validates that candidate patch does not modify the locked test and is structurally safe.
    """
    client = genai.Client()
    phase = state["current_phase"]

    if phase == "reproduction":
        system_instruction = (
            "You are a strict security and quality auditor reviewing a regression test in zero-context mode.\n"
            "Evaluate whether the test effectively tests the issue using in-memory mocks without network calls, "
            "eval/exec, or trivial tautologies (such as assert True).\n"
            "Respond in JSON format with: audit_verdict ('APPROVE' or 'REJECT') and critique (string)."
        )
        audit_payload = {
            "phase": phase,
            "problem_statement": state["problem_statement"],
            "candidate_test_path": state.get("candidate_test_path"),
            "candidate_test_code": state.get("candidate_test_code"),
        }
    else:
        system_instruction = (
            "You are a strict security and quality auditor reviewing a code patch in zero-context mode.\n"
            "Ensure the patch strictly resolves the problem, contains no destructive or unsafe calls, "
            "and DOES NOT modify the locked test file.\n"
            "Respond in JSON format with: audit_verdict ('APPROVE' or 'REJECT') and critique (string)."
        )
        audit_payload = {
            "phase": phase,
            "problem_statement": state["problem_statement"],
            "locked_test_path": state["locked_test_path"],
            "candidate_patch": state.get("candidate_patch"),
            "database_migration_artifacts": state.get("database_migration_artifacts", []),
        }

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=json.dumps(audit_payload),
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.0,
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text or ""
    try:
        parsed = json.loads(raw_text)
    except Exception:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        parsed = (
            json.loads(match.group(0))
            if match
            else {"audit_verdict": "REJECT", "critique": "Parse failure."}
        )

    verdict = parsed.get("audit_verdict", "REJECT").upper()
    critique = parsed.get("critique", "")

    if phase == "resolution" and verdict == "APPROVE":
        if state["locked_test_path"] in state.get("candidate_patch", ""):
            verdict = "REJECT"
            critique = "Security violation: patch attempts to alter the locked regression test."

    updates: dict[str, Any] = {
        "audit_verdict": verdict,
        "auditor_critique": critique,
    }

    if verdict == "REJECT":
        updates["programmer_feedback"] = critique
        updates["stagnation_counter"] = state.get("stagnation_counter", 0) + 1

    return updates


def node_sandbox_execution(state: WorkflowState) -> dict[str, Any]:
    """
    Executes tests inside the isolated hermetic container.
    Phase 1: Success requires the test to FAIL on baseline code (confirming reproduction).
    Phase 2: Success requires all tests to PASS with patch applied.
    """
    sandbox = PythonHermeticSandbox(WORKSPACE_ROOT)
    phase = state["current_phase"]

    if phase == "reproduction":
        test_path = state["candidate_test_path"]
        test_code = state["candidate_test_code"]
        target_file = WORKSPACE_ROOT / test_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(test_code, encoding="utf-8")

        exit_code, output = sandbox.run_hermetic_pytest(test_path=test_path, patch_content=None)

        has_syntax_error = "SyntaxError" in output or "IndentationError" in output
        reproduction_succeeded = (exit_code != 0) and not has_syntax_error

        if reproduction_succeeded:
            logger.info("Reproduction confirmed. Freezing regression test.")
            return {
                "sandbox_passed": True,
                "sandbox_output": output,
                "is_test_locked": True,
                "locked_test_path": test_path,
                "locked_test_code": test_code,
                "current_phase": "resolution",
                "stagnation_counter": 0,
                "consultant_cycles": 0,
                "programmer_feedback": "",
            }
        else:
            feedback = (
                "Test passed on baseline code (failed to reproduce) or encountered syntax error."
            )
            return {
                "sandbox_passed": False,
                "sandbox_output": output,
                "programmer_feedback": f"{feedback}\nOutput:\n{output}",
                "stagnation_counter": state.get("stagnation_counter", 0) + 1,
            }

    else:
        test_path = state["locked_test_path"]
        test_code = state["locked_test_code"]
        target_file = WORKSPACE_ROOT / test_path
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(test_code, encoding="utf-8")

        exit_code, output = sandbox.run_hermetic_pytest(
            test_path=test_path,
            patch_content=state.get("candidate_patch", ""),
            run_full_suite=True,
        )

        all_tests_passed = exit_code == 0
        if all_tests_passed:
            logger.info("All tests passed successfully in hermetic runtime.")
            return {
                "sandbox_passed": True,
                "sandbox_output": output,
                "stagnation_counter": 0,
                "programmer_feedback": "",
            }
        else:
            return {
                "sandbox_passed": False,
                "sandbox_output": output,
                "programmer_feedback": f"Tests failed under applied patch:\n{output}",
                "stagnation_counter": state.get("stagnation_counter", 0) + 1,
            }


def node_consultant(state: WorkflowState) -> dict[str, Any]:
    """
    Architectural consultant node triggered programmatically upon reaching
    the technical stagnation threshold. Formulates an alternate resolution path.
    """
    client = genai.Client()
    tools = _get_mcp_tools_for_llm()

    system_instruction = (
        "You are a principal software architect acting as a technical consultant.\n"
        "The automated debug flow has stagnated. Analyze the failure outputs, critique, "
        "and codebase to provide actionable architectural directions for the programmer.\n"
        "Return a JSON object containing: consultant_guidance (detailed instructions) and root_cause_analysis."
    )

    consultant_payload = {
        "issue_id": state["issue_id"],
        "problem_statement": state["problem_statement"],
        "current_phase": state["current_phase"],
        "programmer_feedback": state.get("programmer_feedback"),
        "sandbox_output": state.get("sandbox_output"),
        "auditor_critique": state.get("auditor_critique"),
    }

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=json.dumps(consultant_payload),
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text or ""
    try:
        parsed = json.loads(raw_text)
    except Exception:
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        parsed = json.loads(match.group(0)) if match else {"consultant_guidance": raw_text}

    return {
        "consultant_guidance": parsed.get("consultant_guidance", raw_text),
        "stagnation_counter": 0,
        "consultant_cycles": state.get("consultant_cycles", 0) + 1,
    }


def node_publish_and_index(state: WorkflowState) -> dict[str, Any]:
    """
    Commits verified code, locked test, and database migration artifacts into
    a timestamped Git branch and performs an autonomous push.
    """
    timestamp_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    clean_issue_id = re.sub(r"[^a-zA-Z0-9_-]", "", state["issue_id"]).lower()
    branch_name = f"fix/issue-{clean_issue_id}-{timestamp_str}"

    sandbox = PythonHermeticSandbox(WORKSPACE_ROOT)
    sandbox.clean_workspace()

    if state.get("candidate_patch"):
        patch_file = WORKSPACE_ROOT / ".final_publish.patch"
        try:
            patch_file.write_text(state["candidate_patch"], encoding="utf-8")
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(patch_file)],
                cwd=WORKSPACE_ROOT,
                check=True,
            )
        finally:
            if patch_file.exists():
                patch_file.unlink()

    test_target = WORKSPACE_ROOT / state["locked_test_path"]
    test_target.parent.mkdir(parents=True, exist_ok=True)
    test_target.write_text(state["locked_test_code"], encoding="utf-8")

    subprocess.run(["git", "checkout", "-b", branch_name], cwd=WORKSPACE_ROOT, check=True)
    subprocess.run(["git", "add", "."], cwd=WORKSPACE_ROOT, check=True)

    commit_msg = (
        f"fix({clean_issue_id}): resolve issue {state['issue_id']}\n\n"
        f"- Frozen regression test: {state['locked_test_path']}\n"
        f"- Verified hermetically in container runtime\n"
        f"- Database migration artifacts: {state.get('database_migration_artifacts', [])}\n"
    )

    subprocess.run(["git", "commit", "-m", commit_msg], cwd=WORKSPACE_ROOT, check=True)

    push_res = subprocess.run(
        ["git", "push", "origin", branch_name],
        cwd=WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if push_res.returncode != 0:
        logger.warning("Git push skipped or non-zero return: %s", push_res.stderr)

    return {
        "target_branch": branch_name,
        "commit_message": commit_msg,
        "execution_status": "SUCCESS",
        "final_solution": f"Successfully published fix to branch {branch_name}.",
    }


def route_after_audit(state: WorkflowState) -> str:
    """
    Routes flow based on blind audit outcome. Rejections trigger stagnation
    counter evaluations leading to programmer retry or consultant escalation.
    """
    if state["audit_verdict"] == "APPROVE":
        return "node_sandbox_execution"

    if state.get("stagnation_counter", 0) >= STAGNATION_THRESHOLD:
        if state.get("consultant_cycles", 0) >= MAX_CONSULTANT_CYCLES:
            return END
        return "node_consultant"

    return "node_programmer"


def route_after_sandbox(state: WorkflowState) -> str:
    """
    Routes flow based on sandbox execution outcome and current workflow phase.
    """
    phase = state["current_phase"]
    sandbox_passed = state["sandbox_passed"]

    if phase == "resolution":
        if sandbox_passed:
            return "node_publish_and_index"
        if state.get("stagnation_counter", 0) >= STAGNATION_THRESHOLD:
            if state.get("consultant_cycles", 0) >= MAX_CONSULTANT_CYCLES:
                return END
            return "node_consultant"
        return "node_programmer"

    if sandbox_passed:
        return "node_programmer"

    if state.get("stagnation_counter", 0) >= STAGNATION_THRESHOLD:
        if state.get("consultant_cycles", 0) >= MAX_CONSULTANT_CYCLES:
            return END
        return "node_consultant"

    return "node_programmer"


def build_orchestrator_graph():
    """
    Constructs the LangGraph autonomous debug workflow graph.
    """
    graph = StateGraph(WorkflowState)

    graph.add_node("node_git_sync", node_git_sync)
    graph.add_node("node_programmer", node_programmer)
    graph.add_node("node_blind_auditor", node_blind_auditor)
    graph.add_node("node_sandbox_execution", node_sandbox_execution)
    graph.add_node("node_consultant", node_consultant)
    graph.add_node("node_publish_and_index", node_publish_and_index)

    graph.add_edge(START, "node_git_sync")
    graph.add_edge("node_git_sync", "node_programmer")
    graph.add_edge("node_programmer", "node_blind_auditor")

    graph.add_conditional_edges(
        "node_blind_auditor",
        route_after_audit,
        {
            "node_sandbox_execution": "node_sandbox_execution",
            "node_consultant": "node_consultant",
            "node_programmer": "node_programmer",
            END: END,
        },
    )

    graph.add_conditional_edges(
        "node_sandbox_execution",
        route_after_sandbox,
        {
            "node_publish_and_index": "node_publish_and_index",
            "node_consultant": "node_consultant",
            "node_programmer": "node_programmer",
            END: END,
        },
    )

    graph.add_edge("node_consultant", "node_programmer")
    graph.add_edge("node_publish_and_index", END)

    return graph.compile()
