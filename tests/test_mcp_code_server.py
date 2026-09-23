from unittest.mock import MagicMock, patch

import pytest

from src.mcp_code_server import (
    _validate_safe_path,
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


def test_read_source_file_success(tmp_path, monkeypatch):
    monkeypatch.setattr("src.mcp_code_server.WORKSPACE_ROOT", tmp_path)
    sample_file = tmp_path / "src" / "sample.py"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text(
        "def first():\n    return 1\ndef second():\n    return 2\n", encoding="utf-8"
    )

    output = read_source_file("src/sample.py", start_line=1, end_line=2)

    assert "def first():" in output
    assert "return 1" in output
    assert "def second():" not in output


def test_read_source_file_path_traversal_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr("src.mcp_code_server.WORKSPACE_ROOT", tmp_path)

    with pytest.raises(PermissionError):
        _validate_safe_path("../outside.py")


def test_read_source_file_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr("src.mcp_code_server.WORKSPACE_ROOT", tmp_path)

    with pytest.raises(FileNotFoundError):
        _validate_safe_path("non_existent_file.py")
