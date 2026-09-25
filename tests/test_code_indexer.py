from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.code_indexer import QDRANT_COLLECTION_NAME, PythonStructuralIndexer


@pytest.fixture
def mock_external_deps() -> Generator[dict[str, MagicMock], None, None]:
    with (
        patch("src.code_indexer.psycopg2.connect") as mock_conn_func,
        patch("src.code_indexer.psycopg2.extras.execute_batch") as mock_batch,
        patch("src.code_indexer.QdrantClient") as mock_qdrant_cls,
        patch("src.code_indexer.TextEmbedding") as mock_embed_cls,
    ):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_conn.__enter__.return_value = mock_conn
        mock_conn_func.return_value = mock_conn

        mock_qdrant = MagicMock()
        mock_qdrant.get_collections.return_value.collections = []
        mock_qdrant_cls.return_value = mock_qdrant

        mock_embed = MagicMock()
        mock_array = MagicMock()
        mock_array.tolist.return_value = [0.1] * 384
        mock_embed.embed.return_value = [mock_array]
        mock_embed_cls.return_value = mock_embed

        yield {
            "connect": mock_conn_func,
            "cursor": mock_cursor,
            "execute_batch": mock_batch,
            "qdrant": mock_qdrant,
            "embed": mock_embed,
        }


def test_init_with_defaults_and_existing_collection(
    mock_external_deps: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    existing_collection = MagicMock()
    existing_collection.name = QDRANT_COLLECTION_NAME
    mock_external_deps["qdrant"].get_collections.return_value.collections = [existing_collection]

    indexer = PythonStructuralIndexer()

    assert indexer.repo_path == Path("/workspace").resolve()
    mock_external_deps["qdrant"].create_collection.assert_not_called()
    assert mock_external_deps["cursor"].execute.call_count >= 2


def test_extract_symbols_and_calls_with_top_level_and_nested(
    mock_external_deps: dict[str, MagicMock],
) -> None:
    code_content = (
        "top_level_call()\n"
        "class WorkerService:\n"
        "    def run_job(self, task):\n"
        "        self.execute(task)\n"
        "        return finish(task)\n"
        "def finish(task):\n"
        "    return True\n"
    )
    indexer = PythonStructuralIndexer(repo_path="/workspace")
    symbols, calls = indexer._extract_symbols_and_calls("worker_service.py", code_content)

    names = {s["name"] for s in symbols}
    assert "WorkerService" in names
    assert "run_job" in names
    assert "finish" in names

    callee_names = {c["callee_name"] for c in calls}
    assert "execute" in callee_names
    assert "finish" in callee_names
    assert "top_level_call" not in callee_names


def test_sync_project_files_empty_list_noop(mock_external_deps: dict[str, MagicMock]) -> None:
    indexer = PythonStructuralIndexer(repo_path="/workspace")
    indexer.sync_project_files(files_to_sync=[])
    mock_external_deps["qdrant"].upsert.assert_not_called()
    mock_external_deps["cursor"].execute.reset_mock()


def test_sync_project_files_no_files_found(
    mock_external_deps: dict[str, MagicMock], tmp_path: Path
) -> None:
    indexer = PythonStructuralIndexer(repo_path=tmp_path)
    indexer.sync_project_files()
    mock_external_deps["qdrant"].upsert.assert_not_called()


def test_sync_project_files_rglob_and_hidden_filtering(
    mock_external_deps: dict[str, MagicMock], tmp_path: Path
) -> None:
    hidden_dir = tmp_path / ".hidden"
    hidden_dir.mkdir(parents=True, exist_ok=True)
    (hidden_dir / "ignored.py").write_text("def secret(): pass\n", encoding="utf-8")

    normal_dir = tmp_path / "pkg"
    normal_dir.mkdir(parents=True, exist_ok=True)
    valid_file = normal_dir / "module.py"
    valid_file.write_text("def active(): pass\n", encoding="utf-8")

    indexer = PythonStructuralIndexer(repo_path=tmp_path)
    indexer.sync_project_files()

    mock_external_deps["cursor"].execute.assert_any_call(
        "DELETE FROM code_symbols WHERE file_path = %s;", ("pkg/module.py",)
    )
    mock_external_deps["qdrant"].upsert.assert_called_once()


def test_sync_project_files_read_error_handled(
    mock_external_deps: dict[str, MagicMock], tmp_path: Path
) -> None:
    indexer = PythonStructuralIndexer(repo_path=tmp_path)
    unreadable = tmp_path / "unreadable.py"
    unreadable.write_text("content", encoding="utf-8")

    with patch.object(Path, "read_text", side_effect=OSError("Read failure")):
        indexer.sync_project_files(files_to_sync=["unreadable.py"])

    mock_external_deps["qdrant"].upsert.assert_not_called()


def test_sync_project_files_empty_python_file(
    mock_external_deps: dict[str, MagicMock], tmp_path: Path
) -> None:
    indexer = PythonStructuralIndexer(repo_path=tmp_path)
    empty_file = tmp_path / "empty.py"
    empty_file.write_text("# only comments\n", encoding="utf-8")

    indexer.sync_project_files(files_to_sync=["empty.py"])

    mock_external_deps["cursor"].execute.assert_any_call(
        "DELETE FROM code_symbols WHERE file_path = %s;", ("empty.py",)
    )
    mock_external_deps["qdrant"].upsert.assert_not_called()


def test_sync_project_files_with_symbols_and_dependencies(
    mock_external_deps: dict[str, MagicMock], tmp_path: Path
) -> None:
    indexer = PythonStructuralIndexer(repo_path=tmp_path)
    source_file = tmp_path / "service.py"
    source_file.write_text(
        "def run_task():\n    step_one()\n",
        encoding="utf-8",
    )

    indexer.sync_project_files(files_to_sync=["service.py"], repo_dir=tmp_path)

    mock_external_deps["cursor"].execute.assert_any_call(
        "DELETE FROM code_symbols WHERE file_path = %s;", ("service.py",)
    )
    assert mock_external_deps["execute_batch"].call_count == 2
    mock_external_deps["qdrant"].upsert.assert_called_once()


def test_sync_project_files_with_symbols_no_dependencies(
    mock_external_deps: dict[str, MagicMock], tmp_path: Path
) -> None:
    indexer = PythonStructuralIndexer(repo_path=tmp_path)
    source_file = tmp_path / "constants.py"
    source_file.write_text("def get_value():\n    return 42\n", encoding="utf-8")

    indexer.sync_project_files(files_to_sync=["constants.py"])

    assert mock_external_deps["execute_batch"].call_count == 1
    mock_external_deps["qdrant"].upsert.assert_called_once()


def test_fetch_dependent_callers_empty_and_duplicates(
    mock_external_deps: dict[str, MagicMock],
) -> None:
    indexer = PythonStructuralIndexer(repo_path="/workspace")
    assert indexer._fetch_dependent_callers([]) == {}

    mock_external_deps["cursor"].fetchall.return_value = [
        ("target_sym", "parent_fn", "parent.py", 10),
        ("target_sym", "parent_fn", "parent.py", 10),
    ]

    res = indexer._fetch_dependent_callers(["target_sym"], max_depth=2)
    assert len(res["target_sym"]) == 1
    assert res["target_sym"][0] == "parent_fn (parent.py:10)"


def test_query_semantic_sources_with_and_without_symbol_id(
    mock_external_deps: dict[str, MagicMock],
) -> None:
    point_with_id = MagicMock()
    point_with_id.payload = {
        "symbol_id": "service.py::run::1",
        "name": "run",
        "file_path": "service.py",
        "symbol_type": "function",
        "start_line": 1,
        "end_line": 3,
        "content": "def run(): pass",
    }
    point_with_id.score = 0.92

    point_without_id = MagicMock()
    point_without_id.payload = {"name": "orphan"}
    point_without_id.score = 0.40

    search_result = MagicMock()
    search_result.points = [point_with_id, point_without_id]
    mock_external_deps["qdrant"].query_points.return_value = search_result

    mock_external_deps["cursor"].fetchall.return_value = [
        ("service.py::run::1", "caller_node", "main.py", 12)
    ]

    indexer = PythonStructuralIndexer(repo_path="/workspace")
    res = indexer.query_semantic_sources("execute task", top_k=2)

    assert "semantic_sources" in res
    assert len(res["semantic_sources"]) == 1
    assert res["semantic_sources"][0]["symbol_id"] == "service.py::run::1"
    assert "caller_node (main.py:12)" in res["semantic_sources"][0]["dependent_callers"]


def test_get_symbol_blast_radius_not_found(mock_external_deps: dict[str, MagicMock]) -> None:
    mock_external_deps["cursor"].fetchall.return_value = []
    indexer = PythonStructuralIndexer(repo_path="/workspace")

    res = indexer.get_symbol_blast_radius("missing_symbol")
    assert res["found"] is False
    assert res["callers"] == []


def test_get_symbol_blast_radius_found_with_duplicates(
    mock_external_deps: dict[str, MagicMock],
) -> None:
    mock_external_deps["cursor"].fetchall.side_effect = [
        [
            ("sym_1", "worker.py", "function", 5, 10),
            ("sym_2", "worker.py", "function", 15, 20),
        ],
        [
            ("sym_1", "shared_caller", "manager.py", 30),
            ("sym_2", "shared_caller", "manager.py", 30),
            ("sym_2", "unique_caller", "api.py", 45),
        ],
    ]

    indexer = PythonStructuralIndexer(repo_path="/workspace")
    res = indexer.get_symbol_blast_radius("target_symbol", max_depth=3)

    assert res["found"] is True
    assert len(res["definitions"]) == 2
    assert "shared_caller (manager.py:30)" in res["dependent_callers"]
    assert "unique_caller (api.py:45)" in res["dependent_callers"]
    assert len(res["dependent_callers"]) == 2
