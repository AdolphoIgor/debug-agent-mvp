import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from src.code_indexer import PythonStructuralIndexer

load_dotenv()

SERVER_HOST = os.getenv("MCP_SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8080"))
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace")).resolve()

mcp = FastMCP(
    "code-intelligence-server",
    host=SERVER_HOST,
    port=SERVER_PORT,
)

_indexer_instance: PythonStructuralIndexer | None = None


def get_indexer() -> PythonStructuralIndexer:
    global _indexer_instance
    if _indexer_instance is None:
        _indexer_instance = PythonStructuralIndexer(
            repo_path=str(WORKSPACE_ROOT),
            postgres_host=os.getenv("POSTGRES_HOST", "postgres"),
            postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
            postgres_db=os.getenv("POSTGRES_DB", "mvp_db"),
            postgres_user=os.getenv("POSTGRES_USER", "mvp_user"),
            postgres_password=os.getenv("POSTGRES_PASSWORD", "mvp_password"),
            qdrant_url=os.getenv("QDRANT_URL", "http://qdrant:6333"),
        )
    return _indexer_instance


def _validate_safe_path(target_file_path: str) -> Path:
    raw_path = Path(target_file_path)
    if raw_path.is_absolute():
        resolved_path = raw_path.resolve()
    else:
        resolved_path = (WORKSPACE_ROOT / raw_path).resolve()

    try:
        resolved_path.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise PermissionError(f"Path traversal access denied: {target_file_path}") from exc

    rel_parts = resolved_path.relative_to(WORKSPACE_ROOT).parts
    if any(part.startswith(".") for part in rel_parts):
        raise PermissionError(
            f"Access to hidden or configuration files is restricted: {target_file_path}"
        )

    if not resolved_path.is_file():
        raise FileNotFoundError(f"File not found: {target_file_path}")

    return resolved_path


@mcp.tool()
def search_codebase(
    issue_description: str,
    top_k: int = 5,
    max_caller_depth: int = 2,
) -> dict[str, Any]:
    """
    Performs semantic vector search combined with blast radius call hierarchy analysis
    to discover code symbols relevant to the problem description.
    """
    indexer = get_indexer()
    return indexer.query_semantic_sources(
        issue_description=issue_description,
        top_k=top_k,
        max_caller_depth=max_caller_depth,
    )


@mcp.tool()
def get_symbol_blast_radius(
    symbol_name: str,
    max_caller_depth: int = 3,
) -> dict[str, Any]:
    """
    Retrieves direct and indirect caller dependencies for a symbol to evaluate change impact.
    """
    indexer = get_indexer()
    return indexer.get_symbol_blast_radius(
        symbol_name=symbol_name,
        max_depth=max_caller_depth,
    )


@mcp.tool()
def read_source_file(
    file_path: str,
    start_line: int = 1,
    end_line: int = -1,
) -> str:
    """
    Reads file content or a slice of lines from the repository with strict path validation.
    """
    validated_path = _validate_safe_path(file_path)

    with open(validated_path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total_lines = len(lines)
    if start_line < 1:
        start_line = 1

    if end_line == -1 or end_line > total_lines:
        end_line = total_lines

    if start_line > total_lines:
        return f"[Empty selection: start_line {start_line} exceeds total lines {total_lines}]"

    selected_lines = lines[start_line - 1 : end_line]
    formatted_content = "".join(
        f"{idx:4d} | {line}" for idx, line in enumerate(selected_lines, start=start_line)
    )

    return (
        f"--- File: {validated_path.relative_to(WORKSPACE_ROOT)} "
        f"(Lines {start_line}-{end_line} of {total_lines}) ---\n"
        f"{formatted_content}"
    )


if __name__ == "__main__":
    mcp.run(transport="sse")
