from unittest.mock import MagicMock, patch

import pytest

import src.mcp_code_server as mcp_server
from src.mcp_code_server import (
    _validate_safe_path,
    get_indexer,
    get_symbol_blast_radius,
    read_source_file,
    search_codebase,
)


@pytest.fixture
def mock_indexer():
    with patch("src.mcp_code_server.get_indexer") as mock_get:
        indexer_instance = MagicMock()
        mock_get.return_value = indexer_instance
        yield indexer_instance


def test_get_indexer_initializes_singleton(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_server, "_indexer_instance", None)
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)

    with patch("src.mcp_code_server.PythonStructuralIndexer") as mock_indexer_cls:
        instance_mock = MagicMock()
        mock_indexer_cls.return_value = instance_mock

        first_call = get_indexer()
        second_call = get_indexer()

        mock_indexer_cls.assert_called_once()
        assert first_call is instance_mock
        assert second_call is instance_mock


def test_search_codebase_delegates_to_indexer(mock_indexer):
    mock_indexer.query_semantic_sources.return_value = {
        "query": "Fix null pointer in serialization",
        "semantic_sources": [{"name": "serialize_data", "file_path": "src/service.py"}],
    }
    result = search_codebase("Fix null pointer in serialization", top_k=3, max_caller_depth=1)
    mock_indexer.query_semantic_sources.assert_called_once_with(
        issue_description="Fix null pointer in serialization",
        top_k=3,
        max_caller_depth=1,
    )
    assert "semantic_sources" in result
    assert result["semantic_sources"][0]["name"] == "serialize_data"


def test_get_symbol_blast_radius_delegates_to_indexer(mock_indexer):
    mock_indexer.get_symbol_blast_radius.return_value = {
        "symbol_name": "compute_total",
        "found": True,
        "dependent_callers": ["process_order (src/order.py:42)"],
    }
    result = get_symbol_blast_radius("compute_total", max_caller_depth=2)
    mock_indexer.get_symbol_blast_radius.assert_called_once_with(
        symbol_name="compute_total",
        max_depth=2,
    )
    assert result["found"] is True
    assert len(result["dependent_callers"]) == 1


def test_validate_safe_path_accepts_absolute_path_within_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    sample_file = tmp_path / "valid.py"
    sample_file.write_text("a = 1\n", encoding="utf-8")

    resolved = _validate_safe_path(str(sample_file.resolve()))
    assert resolved == sample_file.resolve()


def test_validate_safe_path_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(PermissionError, match="Path traversal access denied"):
        _validate_safe_path("../outside.py")


def test_validate_safe_path_rejects_hidden_files(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    hidden_file = tmp_path / ".env"
    hidden_file.write_text("SECRET=123\n", encoding="utf-8")

    with pytest.raises(
        PermissionError, match="Access to hidden or configuration files is restricted"
    ):
        _validate_safe_path(".env")


def test_validate_safe_path_rejects_non_existent_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="File not found"):
        _validate_safe_path("non_existent_file.py")


def test_read_source_file_success(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    sample_file = tmp_path / "src" / "sample.py"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text(
        "def first():\n    return 1\ndef second():\n    return 2\n",
        encoding="utf-8",
    )
    output = read_source_file("src/sample.py", start_line=1, end_line=2)
    assert "--- File: src/sample.py (Lines 1-2 of 4) ---" in output
    assert "   1 | def first():" in output
    assert "   2 |     return 1" in output
    assert "def second():" not in output


def test_read_source_file_boundary_clamping(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    sample_file = tmp_path / "short.py"
    sample_file.write_text("line1\nline2\n", encoding="utf-8")

    output = read_source_file("short.py", start_line=-5, end_line=100)
    assert "--- File: short.py (Lines 1-2 of 2) ---" in output
    assert "   1 | line1" in output
    assert "   2 | line2" in output


def test_read_source_file_start_line_exceeds_total(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "WORKSPACE_ROOT", tmp_path)
    sample_file = tmp_path / "short.py"
    sample_file.write_text("line1\n", encoding="utf-8")

    output = read_source_file("short.py", start_line=10, end_line=15)
    assert "[Empty selection: start_line 10 exceeds total lines 1]" in output
