from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

# Deterministic runtime path resolution for sibling imports within the src directory
CURRENT_DIR: Path = Path(__file__).resolve().parent
SRC_PATH: str = str(CURRENT_DIR)
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

import psycopg2
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

from orchestrator_graph import (
    ExecutionLockManager,
    OrchestratorInput,
    OrchestratorState,
    build_mvp_showcase_graph,
)

logger = logging.getLogger("debug_agent_mvp.api")
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)

# Explicit generic parameterization for compiled state graph instances
_compiled_graph: (
    CompiledStateGraph[OrchestratorState, Any, OrchestratorInput, OrchestratorState] | None
) = None


class RemediationRequest(BaseModel):
    prompt_your_codebase: str = Field(
        ...,
        min_length=5,
        max_length=4000,
        description="Technical prompt describing the codebase defect or feature to be remediated.",
        examples=[
            "Fix the zero division error when calculating moving averages in analytics/metrics.py"
        ],
    )


class RemediationResponse(BaseModel):
    status: str = Field(description="Final execution outcome (COMPLETED or FAILED).")
    ticket_id: str = Field(description="Deterministic run identifier.")
    branch_name: str = Field(description="Target branch created for the remediation.")
    tests_passed: bool = Field(description="Indicates whether all hermetic unit tests passed.")
    sandbox_logs: str = Field(
        description="Standard output and error captured during container test execution."
    )
    unified_diff: str | None = Field(
        default=None, description="Unified diff patch applied to the codebase."
    )
    audit_verdict: str | None = Field(
        default=None, description="Verdict returned by the blind antagonist auditor."
    )
    programmer_feedback: str | None = Field(
        default=None, description="Diagnostic telemetry recorded during the run."
    )


class HealthCheckResponse(BaseModel):
    status: str
    postgres_connected: bool
    qdrant_connected: bool
    orchestrator_locked: bool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _compiled_graph
    logger.info("Compiling StateGraph instance for FastAPI gateway...")
    try:
        builder: StateGraph[OrchestratorState, Any, OrchestratorInput, OrchestratorState] = (
            build_mvp_showcase_graph()
        )
        _compiled_graph = builder.compile()
        logger.info("StateGraph successfully compiled and ready for execution.")
    except Exception as exc:
        logger.critical("Fatal error compiling StateGraph: %s", str(exc), exc_info=True)
        raise RuntimeError("StateGraph compilation failed.") from exc
    yield
    _compiled_graph = None
    logger.info("FastAPI gateway shutdown complete.")


app = FastAPI(
    title="DEBUG-AGENT-MVP API",
    description="Dedicated programmatic REST API gateway for automated AST-guided codebase debugging.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(
    "/health",
    response_model=HealthCheckResponse,
    tags=["System"],
    summary="Service health and dependency connectivity verification",
)
async def health_check() -> HealthCheckResponse:
    postgres_ok: bool = False
    qdrant_ok: bool = False
    is_locked: bool = False

    db_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql://mvp_user:mvp_password@localhost:5432/client_baseline_db",
    )
    try:
        conn = psycopg2.connect(db_url)
        conn.close()
        postgres_ok = True
    except Exception as exc:
        logger.warning("PostgreSQL healthcheck check failed: %s", str(exc))

    qdrant_url: str = os.environ.get("QDRANT_URL", "http://localhost:6333")
    try:
        client = QdrantClient(url=qdrant_url, timeout=2)
        client.get_collections()
        qdrant_ok = True
    except Exception as exc:
        logger.warning("Qdrant healthcheck check failed: %s", str(exc))

    try:
        ExecutionLockManager.acquire()
        ExecutionLockManager.release()
    except RuntimeError:
        is_locked = True

    overall_status: str = "HEALTHY" if (postgres_ok and qdrant_ok and not is_locked) else "DEGRADED"

    return HealthCheckResponse(
        status=overall_status,
        postgres_connected=postgres_ok,
        qdrant_connected=qdrant_ok,
        orchestrator_locked=is_locked,
    )


@app.post(
    "/api/v1/debug/remediate",
    response_model=RemediationResponse,
    status_code=status.HTTP_200_OK,
    tags=["Orchestration"],
    summary="Execute end-to-end automated codebase remediation",
)
async def remediate_codebase(request: RemediationRequest) -> RemediationResponse:
    if _compiled_graph is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="StateGraph engine has not been initialized.",
        )

    active_graph: CompiledStateGraph[
        OrchestratorState, Any, OrchestratorInput, OrchestratorState
    ] = _compiled_graph
    initial_input: OrchestratorInput = {
        "prompt_your_codebase": request.prompt_your_codebase,
    }

    def _run_workflow() -> dict[str, Any]:
        result: Any = active_graph.invoke(initial_input)
        if isinstance(result, dict):
            return cast(dict[str, Any], result)
        return {}

    try:
        final_state: dict[str, Any] = await asyncio.to_thread(_run_workflow)
    except RuntimeError as r_exc:
        if "Another active orchestration process holds the global system lock" in str(r_exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="System execution lock is actively held by another process. Concurrent execution is rejected.",
            ) from r_exc
        logger.error("Runtime failure during graph invocation: %s", str(r_exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution failed: {str(r_exc)}",
        ) from r_exc
    except Exception as exc:
        logger.critical("Uncaught exception during remediation run: %s", str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal error occurred during graph execution.",
        ) from exc

    current_patch: Any = final_state.get("current_patch")
    last_audit: Any = final_state.get("last_audit")

    execution_status: str = "COMPLETED" if final_state.get("tests_passed") else "FAILED"

    return RemediationResponse(
        status=execution_status,
        ticket_id=str(final_state.get("ticket_id", "unknown_ticket")),
        branch_name=str(final_state.get("branch_name", "unknown_branch")),
        tests_passed=bool(final_state.get("tests_passed", False)),
        sandbox_logs=str(final_state.get("sandbox_logs", "No logs emitted.")),
        unified_diff=getattr(current_patch, "unified_diff", None),
        audit_verdict=getattr(last_audit, "verdict", None),
        programmer_feedback=final_state.get("programmer_feedback"),
    )
