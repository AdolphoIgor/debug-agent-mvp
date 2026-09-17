from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def configure_test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key-value")
    monkeypatch.setenv("GCP_PROJECT_ID", "test-gcp-project")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("MCP_SERVER_SSE_URL", "http://localhost:8080/sse")
    monkeypatch.setenv("SANDBOX_IMAGE", "mvp-sandbox:test")
    monkeypatch.setenv("SANDBOX_TIMEOUT_SEC", "15")

    lock_file = tmp_path / "mvp_orchestrator_test.lock"
    monkeypatch.setattr("orchestrator_graph.LOCK_FILE_PATH", lock_file)


@pytest.fixture
def mock_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    git_dir = ws / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (ws / "main.py").write_text("def run():\n    return True\n", encoding="utf-8")
    return ws


@pytest.fixture
def mock_gemini_client() -> Generator[MagicMock, None, None]:
    client_mock = MagicMock()
    yield client_mock
