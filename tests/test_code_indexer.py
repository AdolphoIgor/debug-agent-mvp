from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from code_indexer import ExtractedSymbol, PythonStructuralIndexer


@pytest.fixture
def mock_qdrant_client() -> Generator[MagicMock, None, None]:
    with patch("code_indexer.QdrantClient") as mock_cls:
        client_instance = MagicMock()
        client_instance.get_collections.return_value.collections = []
        mock_cls.return_value = client_instance
        yield client_instance


@pytest.fixture
def mock_embedding_model() -> Generator[MagicMock, None, None]:
    with patch("code_indexer.EmbeddingModelSingleton.get_model") as mock_get_model:
        embed_instance = MagicMock()

        def mock_embed_generator(texts, **kwargs):
            # Yield a mock array with a .tolist() method for each input text
            for _ in texts:
                mock_array = MagicMock()
                mock_array.tolist.return_value = [0.1] * 384
                yield mock_array

        embed_instance.embed.side_effect = mock_embed_generator
        mock_get_model.return_value = embed_instance
        yield embed_instance


@pytest.fixture
def mock_db_pool() -> Generator[MagicMock, None, None]:
    with patch("code_indexer.DatabasePool.get_connection") as mock_conn_ctx:
        mock_conn = MagicMock()
        mock_conn.encoding = "UTF8"

        mock_cursor = MagicMock()
        mock_cursor.connection = mock_conn
        mock_cursor.mogrify.return_value = b"mocked_bytes"

        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn_ctx.return_value.__enter__.return_value = mock_conn
        yield mock_cursor


def test_parse_file_extracts_classes_methods_and_calls(
    tmp_path: Path,
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    sample_file = repo_dir / "service.py"

    code_content = """
class DataProcessor:
    def process_payload(self, raw_data):
        sanitized = self.clean(raw_data)
        return calculate(sanitized)

def calculate(value):
    return value * 2
"""
    sample_file.write_text(code_content, encoding="utf-8")

    indexer = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")
    symbols = indexer.parse_file(
        project_id="default_project", repo_dir=repo_dir, rel_path="service.py"
    )

    assert len(symbols) == 3

    symbol_map: dict[str, ExtractedSymbol] = {s.name: s for s in symbols}
    assert "DataProcessor" in symbol_map
    assert "process_payload" in symbol_map
    assert "calculate" in symbol_map

    proc_symbol = symbol_map["process_payload"]
    assert proc_symbol.symbol_type == "function_definition"
    assert proc_symbol.scope_path == "DataProcessor"
    assert "calculate" in proc_symbol.calls or "self.clean" in proc_symbol.calls


def test_parse_file_ignores_non_python_files(
    tmp_path: Path,
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    sample_file = repo_dir / "README.md"
    sample_file.write_text("# Documentation\n", encoding="utf-8")

    indexer = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")
    symbols = indexer.parse_file(
        project_id="default_project", repo_dir=repo_dir, rel_path="README.md"
    )

    assert symbols == []


def test_parse_file_handles_missing_file(
    tmp_path: Path,
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    indexer = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")
    symbols = indexer.parse_file(
        project_id="default_project", repo_dir=repo_dir, rel_path="absent.py"
    )

    assert symbols == []


def test_qdrant_initialization_creates_collection_when_absent(
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
) -> None:
    mock_qdrant_client.get_collections.return_value.collections = []

    _ = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")

    mock_qdrant_client.create_collection.assert_called_once()
    assert mock_qdrant_client.create_payload_index.call_count == 2


def test_sync_project_files_empty_list_noop(
    tmp_path: Path,
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
) -> None:
    indexer = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")
    indexer.sync_project_files(project_id="default_project", repo_dir=tmp_path, files_to_sync=[])

    mock_qdrant_client.delete.assert_not_called()
    mock_qdrant_client.upsert.assert_not_called()


def test_sync_project_files_deletes_and_upserts(
    tmp_path: Path,
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
    mock_db_pool: MagicMock,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    module_path = repo_dir / "worker.py"
    module_path.write_text("def execute_task():\n    pass\n", encoding="utf-8")

    indexer = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")
    indexer.sync_project_files(
        project_id="default_project", repo_dir=repo_dir, files_to_sync=["worker.py"]
    )

    mock_qdrant_client.delete.assert_called_once()
    mock_qdrant_client.upsert.assert_called_once()
    assert mock_db_pool.execute.call_count >= 2


def test_query_semantic_bug_sources_returns_mapped_callers(
    mock_qdrant_client: MagicMock,
    mock_embedding_model: MagicMock,
    mock_db_pool: MagicMock,
) -> None:
    mock_hit = MagicMock()
    mock_hit.payload = {
        "symbol_id": "default_project::worker.py::::execute_task::12345678",
        "file_path": "worker.py",
        "name": "execute_task",
        "content": "def execute_task(): pass",
    }
    mock_qdrant_client.search.return_value = [mock_hit]
    mock_db_pool.fetchall.return_value = [("caller_id", "main.py", "start_worker")]

    indexer = PythonStructuralIndexer(qdrant_url="http://mock-qdrant:6333")
    result: dict[str, Any] = indexer.query_semantic_bug_sources(
        project_id="default_project", bug_description="Worker execution failed"
    )

    assert "semantic_sources" in result
    sources = result["semantic_sources"]
    assert len(sources) == 1
    assert sources[0]["name"] == "execute_task"
    assert sources[0]["dependent_callers"] == ["start_worker (main.py)"]
