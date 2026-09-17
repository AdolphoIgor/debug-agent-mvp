from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

import psycopg2
from mcp.server.fastmcp import FastMCP
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

mcp = FastMCP("MVP-DB-Access")

FORBIDDEN_SQL_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"\b(INSERT)\b", re.IGNORECASE),
    re.compile(r"\b(UPDATE)\b", re.IGNORECASE),
    re.compile(r"\b(DELETE)\b", re.IGNORECASE),
    re.compile(r"\b(DROP)\b", re.IGNORECASE),
    re.compile(r"\b(ALTER)\b", re.IGNORECASE),
    re.compile(r"\b(TRUNCATE)\b", re.IGNORECASE),
    re.compile(r"\b(GRANT)\b", re.IGNORECASE),
    re.compile(r"\b(REVOKE)\b", re.IGNORECASE),
    re.compile(r"\b(EXECUTE)\b", re.IGNORECASE),
    re.compile(r"\b(CALL)\b", re.IGNORECASE),
]


class DatabaseAccessError(Exception):
    """Raised when a database interaction fails."""
    pass


class UnauthorizedQueryError(Exception):
    """Raised when non-DQL or mutating SQL statements are detected."""
    pass


@contextmanager
def get_db_connection() -> Generator[PgConnection, None, None]:
    db_url: str = os.environ.get(
        "TARGET_DB_URL",
        "postgresql://mvp_user:mvp_password@postgres:5432/client_baseline_db"
    )
    conn: Optional[PgConnection] = None
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        yield conn
    except psycopg2.Error as exc:
        raise DatabaseAccessError(f"Database connection could not be established: {str(exc)}") from exc
    finally:
        if conn is not None and not conn.closed:
            conn.close()


def validate_dql_query(sql_query: str) -> None:
    sanitized: str = sql_query.strip()
    if not sanitized:
        raise UnauthorizedQueryError("Query execution rejected: SQL string is empty.")

    # Validate that execution begins with a valid read operation
    if not re.match(r"^(SELECT|WITH|EXPLAIN)\b", sanitized, re.IGNORECASE):
        raise UnauthorizedQueryError(
            "Query execution rejected: Only read-only statements (SELECT, WITH, EXPLAIN) are permitted."
        )

    for pattern in FORBIDDEN_SQL_PATTERNS:
        if pattern.search(sanitized):
            raise UnauthorizedQueryError(
                f"Query execution rejected: Prohibited SQL token matched ({pattern.pattern})."
            )


@mcp.tool()
def query_database(sql_query: str) -> str:
    """Executes read-only SQL queries on the client database to inspect schemas and data.

    Mutating commands (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE) are forbidden.
    """
    try:
        validate_dql_query(sql_query)
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql_query)
                results: List[Dict[str, Any]] = cur.fetchmany(50)
                if not results:
                    return "Query executed successfully. Zero records returned."
                formatted_lines: List[str] = [str(dict(row)) for row in results]
                return "\n".join(formatted_lines)
    except UnauthorizedQueryError as u_exc:
        return f"Policy Violation: {str(u_exc)}"
    except DatabaseAccessError as db_exc:
        return f"Infrastructure Failure: {str(db_exc)}"
    except Exception as exc:
        return f"Unexpected Execution Error: {str(exc)}"


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8080)
