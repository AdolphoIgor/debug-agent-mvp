from __future__ import annotations

import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import psycopg2
from mcp.server.fastmcp import FastMCP
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor

SERVER_HOST: str = os.environ.get("MCP_HOST", os.environ.get("FASTMCP_HOST", "0.0.0.0"))
SERVER_PORT: int = int(os.environ.get("MCP_PORT", os.environ.get("FASTMCP_PORT", "8080")))

os.environ["FASTMCP_HOST"] = SERVER_HOST
os.environ["FASTMCP_PORT"] = str(SERVER_PORT)

mcp = FastMCP(name="MVP-DB-Access")
if hasattr(mcp, "settings"):
    mcp.settings.host = SERVER_HOST
    mcp.settings.port = SERVER_PORT

FORBIDDEN_SQL_PATTERNS: list[re.Pattern[str]] = [
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
    """Raised when a relational database query or connection fails."""

    pass


class UnauthorizedQueryError(Exception):
    """Raised when non-DQL or mutating SQL statements are detected."""

    pass


@contextmanager
def get_db_connection() -> Generator[PgConnection, None, None]:
    db_url: str = os.environ.get(
        "TARGET_DB_URL",
        "postgresql://mvp_user:mvp_password@postgres:5432/client_baseline_db",
    )
    conn: PgConnection | None = None
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        yield conn
    except psycopg2.Error as exc:
        raise DatabaseAccessError(f"Relational connection failure: {str(exc)}") from exc
    finally:
        if conn is not None and not conn.closed:
            conn.close()


def validate_dql_query(sql_query: str) -> None:
    sanitized: str = sql_query.strip()
    if not sanitized:
        raise UnauthorizedQueryError("Query rejected: SQL statement is empty.")

    if not re.match(r"^(SELECT|WITH|EXPLAIN)\b", sanitized, re.IGNORECASE):
        raise UnauthorizedQueryError(
            "Query rejected: Only read-only operations (SELECT, WITH, EXPLAIN) are permitted."
        )

    for pattern in FORBIDDEN_SQL_PATTERNS:
        if pattern.search(sanitized):
            raise UnauthorizedQueryError(
                f"Query rejected: Mutation token matched ({pattern.pattern})."
            )


@mcp.tool()
def query_database(sql_query: str) -> str:
    """Executes read-only SQL queries on the database to inspect tables and schemas.

    Mutating operations (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE) are rejected.
    """
    try:
        validate_dql_query(sql_query)
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql_query)
                results: list[dict[str, Any]] = cur.fetchmany(50)
                if not results:
                    return "Query executed successfully. Zero records returned."
                return "\n".join(str(dict(row)) for row in results)
    except UnauthorizedQueryError as u_exc:
        return f"Policy Violation: {str(u_exc)}"
    except DatabaseAccessError as db_exc:
        return f"Infrastructure Failure: {str(db_exc)}"
    except Exception as exc:
        return f"Unexpected Execution Error: {str(exc)}"


if __name__ == "__main__":
    mcp.run(transport="sse")
