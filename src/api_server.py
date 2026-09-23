import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.orchestrator_graph import WorkflowState, build_orchestrator_graph

compiled_graph = build_orchestrator_graph()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class WorkflowExecutionRequest(BaseModel):
    issue_id: str
    problem_statement: str


class WorkflowExecutionResponse(BaseModel):
    issue_id: str
    target_branch: str
    execution_status: str
    final_solution: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing API server and verifying agent orchestrator graph.")
    yield
    logger.info("Shutting down API server gracefully.")


app = FastAPI(
    title="Autonomous Debug Agent API",
    version="0.2.0",
    lifespan=lifespan,
)


@app.post("/workflow/run", response_model=WorkflowExecutionResponse)
async def run_workflow(payload: WorkflowExecutionRequest) -> Any:
    """
    Triggers the autonomous debug workflow graph for a given issue and problem statement.
    """
    initial_state: WorkflowState = {
        "issue_id": payload.issue_id,
        "problem_statement": payload.problem_statement,
        "current_phase": "reproduction",
        "is_test_locked": False,
        "locked_test_path": "",
        "locked_test_code": "",
        "candidate_test_path": "",
        "candidate_test_code": "",
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

    try:
        final_state = await compiled_graph.ainvoke(initial_state)
        return {
            "issue_id": final_state.get("issue_id", payload.issue_id),
            "target_branch": final_state.get("target_branch", ""),
            "execution_status": final_state.get("execution_status", "FAILED"),
            "final_solution": final_state.get("final_solution", "Workflow completed."),
        }
    except Exception as exc:
        logger.exception("Workflow execution failed with exception: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy"}
