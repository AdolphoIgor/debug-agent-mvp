from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_db_server import (
    DatabaseAccessError,
    UnauthorizedQueryError,
    query_database,
    validate_dql_query,
)


def test_validate_dql_query_allows_valid_statements() -> None:
    valid_queries = [
        "SELECT * FROM users WHERE active = true;",
        "select id, name from accounts;",
        "WITH active_users AS (SELECT id FROM users) SELECT * FROM active_users;",
        "EXPLAIN SELECT count(*) FROM orders;",
    ]
    for q in valid_queries:
        validate_dql_query(q)


def test_validate_dql_query_rejects_empty_string() -> None:
    with pytest.raises(UnauthorizedQueryError, match="SQL string is empty"):
        validate_dql_query("   ")


def test_validate_dql_query_rejects_non_dql_starting_tokens() -> None:
    disallowed_starts = [
        "PRAGMA table_info(users);",
        "SHOW TABLES;",
        "DESCRIBE accounts;",
    ]
    for q in disallowed_starts:
        with pytest.raises(UnauthorizedQueryError, match="Only read-only statements"):
            validate_dql_query(q)


def test_validate_dql_query_rejects_mutating_keywords() -> None:
    forbidden_queries = [
        "INSERT INTO users (name) VALUES ('hacker');",
        "UPDATE accounts SET balance = 0;",
        "DELETE FROM records WHERE id = 1;",
        "DROP TABLE customers;",
        "ALTER TABLE users ADD COLUMN compromised BOOLEAN;",
        "TRUNCATE system_logs;",
        "GRANT ALL PRIVILEGES ON DATABASE test TO public;",
        "SELECT * FROM users; DROP TABLE users; --",
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity;",
        "CALL run_migration();",
    ]
    for q in forbidden_queries:
        with pytest.raises(UnauthorizedQueryError):
            validate_dql_query(q)


@patch("mcp_db_server.get_db_connection")
def test_query_database_success(mock_conn_ctx: MagicMock) -> None:
    mock_cursor = MagicMock()
    mock_cursor.fetchmany.return_value = [{"id": 1, "username": "admin"}]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn_ctx.return_value.__enter__.return_value = mock_conn

    result = query_database("SELECT id, username FROM users LIMIT 1;")

    assert "{'id': 1, 'username': 'admin'}" in result
    mock_cursor.execute.assert_called_once_with("SELECT id, username FROM users LIMIT 1;")


@patch("mcp_db_server.get_db_connection")
def test_query_database_empty_result_handling(mock_conn_ctx: MagicMock) -> None:
    mock_cursor = MagicMock()
    mock_cursor.fetchmany.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_conn_ctx.return_value.__enter__.return_value = mock_conn

    result = query_database("SELECT * FROM empty_table;")

    assert result == "Query executed successfully. Zero records returned."


def test_query_database_catches_unauthorized_query() -> None:
    result = query_database("SELECT * FROM users; DROP TABLE users;")
    assert "Policy Violation" in result
    assert "Prohibited SQL token matched" in result


@patch("mcp_db_server.get_db_connection")
def test_query_database_catches_database_access_failure(mock_conn_ctx: MagicMock) -> None:
    mock_conn_ctx.side_effect = DatabaseAccessError("PostgreSQL pool connection failed.")
    result = query_database("SELECT 1;")
    assert "Infrastructure Failure" in result
