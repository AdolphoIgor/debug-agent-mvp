from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api_server import app, lifespan


def test_health_check_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


def test_run_workflow_success() -> None:
    mock_final_state = {
        "issue_id": "CORE-101",
        "target_branch": "fix/issue-core-101-20260925",
        "execution_status": "SUCCESS",
        "final_solution": "Patch applied cleanly and verified.",
    }
    with patch("src.api_server.compiled_graph.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.return_value = mock_final_state
        with TestClient(app) as client:
            payload = {
                "issue_id": "CORE-101",
                "problem_statement": "Prevent division by zero in metrics service",
            }
            response = client.post("/workflow/run", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert data["issue_id"] == "CORE-101"
            assert data["target_branch"] == "fix/issue-core-101-20260925"
            assert data["execution_status"] == "SUCCESS"
            assert data["final_solution"] == "Patch applied cleanly and verified."
            mock_ainvoke.assert_awaited_once()


def test_run_workflow_execution_error_returns_500() -> None:
    with patch("src.api_server.compiled_graph.ainvoke", new_callable=AsyncMock) as mock_ainvoke:
        mock_ainvoke.side_effect = RuntimeError("Autonomous agent graph failed execution")
        with TestClient(app) as client:
            payload = {
                "issue_id": "CORE-102",
                "problem_statement": "Fix regression in worker thread",
            }
            response = client.post("/workflow/run", json=payload)
            assert response.status_code == 500
            assert response.json() == {"detail": "Autonomous agent graph failed execution"}


def test_run_workflow_validation_error_returns_422() -> None:
    with TestClient(app) as client:
        response = client.post("/workflow/run", json={"wrong_field": "data"})
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_lifespan_event_logging() -> None:
    with patch("src.api_server.logger") as mock_logger:
        async with lifespan(app):
            mock_logger.info.assert_called_with(
                "Initializing API server and verifying agent orchestrator graph."
            )
        mock_logger.info.assert_called_with("Shutting down API server gracefully.")
