from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

import psycopg2
from mcp.server.fastmcp import FastMCP
from psycopg2.extras import RealDictCursor

mcp = FastMCP("MVP-DB-Access")


@contextmanager
def get_db_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    db_url = os.environ.get(
        "TARGET_DB_URL", "postgresql://mvp_user:mvp_password@postgres:5432/client_baseline_db"
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@mcp.tool()
def query_database(sql_query: str) -> str:
    """
    Executes read-only DQL queries (SELECT) on the client database to inspect
    relational schemas, tables, and data associated with the reported issue.
    Mutating statements (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE) are strictly blocked.
    """
    upper_query = sql_query.strip().upper()
    forbidden_tokens = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
    ]
    if any(token in upper_query for token in forbidden_tokens):
        return "Error: Access denied. Only read-only queries (SELECT) are permitted via MCP."

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql_query)
                results = cur.fetchmany(50)
                if not results:
                    return "Query executed successfully. No records returned."
                lines = [str(dict(row)) for row in results]
                return "\n".join(lines)
    except Exception as exc:
        return f"SQL query execution failed: {str(exc)}"


if __name__ == "__main__":
    server_port = int(os.environ.get("MCP_SERVER_PORT", "8080"))
    mcp.run(transport="sse", host="0.0.0.0", port=server_port)
