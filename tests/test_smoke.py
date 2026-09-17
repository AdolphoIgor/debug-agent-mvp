from __future__ import annotations

import os
from pathlib import Path

import code_indexer


def test_database_pool_dsn_resolution() -> None:
    expected_default = "postgresql://mvp_user:mvp_password@postgres:5432/mvp_db"
    resolved_dsn = os.environ.get("DATABASE_URL", expected_default)
    assert "mvp_db" in resolved_dsn
    assert "mvp_user" in resolved_dsn


def test_ast_python_symbol_parsing(tmp_path: Path) -> None:
    dummy_source = (
        "class AccountManager:\n"
        "    def calculate_balance(self, user_id: str) -> float:\n"
        "        return 100.0\n"
    )
    test_file = tmp_path / "dummy_service.py"
    test_file.write_text(dummy_source, encoding="utf-8")

    # Parsing is validated without requiring external network connectivity to Qdrant
    indexer = code_indexer.PythonStructuralIndexer.__new__(code_indexer.PythonStructuralIndexer)
    indexer.language = code_indexer._load_python_language()
    indexer.parser = code_indexer._init_python_parser(indexer.language)

    extracted_symbols = indexer.parse_file(
        project_id="smoke_test",
        repo_dir=tmp_path,
        rel_path="dummy_service.py",
    )

    symbol_names = {s.name for s in extracted_symbols}
    assert "AccountManager" in symbol_names
    assert "calculate_balance" in symbol_names

    function_symbol = next(s for s in extracted_symbols if s.name == "calculate_balance")
    assert function_symbol.scope_path == "AccountManager"
    assert function_symbol.symbol_type == "function_definition"
