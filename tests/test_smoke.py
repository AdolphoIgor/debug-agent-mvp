from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from tree_sitter_languages import get_language, get_parser

from src.code_indexer import PythonStructuralIndexer
from src.db_pool import DatabasePool


def test_database_pool_dsn_resolution() -> None:
    expected_default = "postgresql://mvp_user:mvp_password@postgres:5432/mvp_db"
    resolved_dsn = os.environ.get("DATABASE_URL", expected_default)
    assert "mvp_db" in resolved_dsn
    assert "mvp_user" in resolved_dsn

    with patch("psycopg2.pool.ThreadedConnectionPool") as mock_pool:
        DatabasePool._pool = None
        DatabasePool.initialize()
        mock_pool.assert_called_once_with(
            minconn=1,
            maxconn=10,
            dsn=resolved_dsn,
        )
        DatabasePool.close_all()


def test_ast_python_symbol_parsing(tmp_path: Path) -> None:
    dummy_source = (
        "class AccountManager:\n"
        "    def calculate_balance(self, user_id: str) -> float:\n"
        "        return 100.0\n"
    )
    indexer = PythonStructuralIndexer.__new__(PythonStructuralIndexer)
    indexer.language = get_language("python")
    indexer.parser = get_parser("python")

    extracted_symbols, extracted_calls = indexer._extract_symbols_and_calls(
        file_path="dummy_service.py",
        code_content=dummy_source,
    )

    symbol_names = {s["name"] for s in extracted_symbols}
    assert "AccountManager" in symbol_names
    assert "calculate_balance" in symbol_names

    function_symbol = next(s for s in extracted_symbols if s["name"] == "calculate_balance")
    assert function_symbol["symbol_type"] == "function"
    assert function_symbol["start_line"] == 2
