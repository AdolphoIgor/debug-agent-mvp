import logging
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from tree_sitter_languages import get_language, get_parser

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MVP_NAMESPACE = uuid.UUID("a3b8c9d0-e1f2-4a5b-8c9d-0e1f2a3b4c5d")
EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
VECTOR_DIMENSION = 384
QDRANT_COLLECTION_NAME = "mvp_codebase"


class PythonStructuralIndexer:
    """
    Indexes Python codebases using tree-sitter AST parsing, persists structural
    and dependency graphs into PostgreSQL, and indexes semantic code symbols into Qdrant.
    """

    def __init__(
        self,
        repo_path: str | Path | None = None,
        repo_dir: str | Path | None = None,
        postgres_host: str = "postgres",
        postgres_port: int = 5432,
        postgres_db: str = "mvp_db",
        postgres_user: str = "mvp_user",
        postgres_password: str = "mvp_password",
        qdrant_url: str = "http://qdrant:6333",
    ):
        target_dir = repo_path if repo_path is not None else repo_dir
        if target_dir is None:
            target_dir = os.getenv("WORKSPACE_ROOT", "/workspace")
        self.repo_path = Path(target_dir).resolve()

        self.postgres_params = {
            "host": postgres_host,
            "port": postgres_port,
            "dbname": postgres_db,
            "user": postgres_user,
            "password": postgres_password,
        }
        self.qdrant_url = qdrant_url

        self.language = get_language("python")
        self.parser = get_parser("python")

        self.embed_model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME)
        self.qdrant_client = QdrantClient(url=self.qdrant_url)

        self._init_relational_schema()
        self._init_vector_collection()

    def _get_db_connection(self):
        return psycopg2.connect(**self.postgres_params)

    def _init_relational_schema(self) -> None:
        """
        Initializes PostgreSQL tables for symbols and directed call graph dependencies.
        """
        create_symbols_table = """
        CREATE TABLE IF NOT EXISTS code_symbols (
            id VARCHAR(64) PRIMARY KEY,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            symbol_type VARCHAR(32) NOT NULL,
            start_line INT NOT NULL,
            end_line INT NOT NULL,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_code_symbols_file ON code_symbols(file_path);
        CREATE INDEX IF NOT EXISTS idx_code_symbols_name ON code_symbols(name);
        """

        create_dependencies_table = """
        CREATE TABLE IF NOT EXISTS code_dependencies (
            id SERIAL PRIMARY KEY,
            caller_symbol_id VARCHAR(64) NOT NULL REFERENCES code_symbols(id) ON DELETE CASCADE,
            callee_name TEXT NOT NULL,
            callee_symbol_id VARCHAR(64) REFERENCES code_symbols(id) ON DELETE SET NULL,
            file_path TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_deps_caller ON code_dependencies(caller_symbol_id);
        CREATE INDEX IF NOT EXISTS idx_deps_callee_name ON code_dependencies(callee_name);
        CREATE INDEX IF NOT EXISTS idx_deps_callee_id ON code_dependencies(callee_symbol_id);
        """

        with self._get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_symbols_table)
                cur.execute(create_dependencies_table)
            conn.commit()

    def _init_vector_collection(self) -> None:
        """
        Initializes the vector collection in Qdrant if it does not already exist.
        """
        collections = self.qdrant_client.get_collections().collections
        collection_names = [col.name for col in collections]

        if QDRANT_COLLECTION_NAME not in collection_names:
            self.qdrant_client.create_collection(
                collection_name=QDRANT_COLLECTION_NAME,
                vectors_config=VectorParams(
                    size=VECTOR_DIMENSION,
                    distance=Distance.COSINE,
                ),
            )

    def _extract_symbols_and_calls(
        self, file_path: str, code_content: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Parses Python code using tree-sitter to extract definitions and call references.
        """
        tree = self.parser.parse(bytes(code_content, "utf8"))
        symbols: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []

        def traverse_node(node, current_enclosing_symbol: str | None = None):
            symbol_id = current_enclosing_symbol

            if node.type in ("function_definition", "class_definition"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    raw_name = name_node.text.decode("utf8")
                    symbol_type = "function" if node.type == "function_definition" else "class"
                    start_line = node.start_point[0] + 1
                    end_line = node.end_point[0] + 1
                    snippet = code_content.splitlines()[start_line - 1 : end_line]
                    content_str = "\n".join(snippet)

                    generated_id = f"{file_path}::{raw_name}::{start_line}"
                    symbol_entry = {
                        "id": generated_id,
                        "file_path": file_path,
                        "name": raw_name,
                        "symbol_type": symbol_type,
                        "start_line": start_line,
                        "end_line": end_line,
                        "content": content_str,
                    }
                    symbols.append(symbol_entry)
                    symbol_id = generated_id

            if node.type == "call":
                func_node = node.child_by_field_name("function")
                if func_node:
                    callee_text = func_node.text.decode("utf8")
                    call_name = callee_text.split(".")[-1]
                    if current_enclosing_symbol:
                        calls.append(
                            {
                                "caller_symbol_id": current_enclosing_symbol,
                                "callee_name": call_name,
                                "file_path": file_path,
                            }
                        )

            for child in node.children:
                traverse_node(child, symbol_id)

        traverse_node(tree.root_node)
        return symbols, calls

    def sync_project_files(
        self,
        files_to_sync: list[str] | None = None,
        repo_dir: str | Path | None = None,
    ) -> None:
        """
        Deterministic incremental sync triggered when a Git delta exists.
        If files_to_sync is empty, execution returns immediately.
        """
        if repo_dir is not None:
            self.repo_path = Path(repo_dir).resolve()

        if files_to_sync is not None and len(files_to_sync) == 0:
            logger.info("No delta detected. Skipping structural and vector reindexing.")
            return

        target_files: list[Path] = []
        if files_to_sync is not None:
            for rel_file in files_to_sync:
                full_path = self.repo_path / rel_file
                if full_path.suffix == ".py" and full_path.is_file():
                    target_files.append(full_path)
        else:
            target_files = [
                p
                for p in self.repo_path.rglob("*.py")
                if not any(part.startswith(".") for part in p.parts)
            ]

        if not target_files:
            logger.info("No valid Python files to index.")
            return

        for path_obj in target_files:
            rel_path = str(path_obj.relative_to(self.repo_path))
            try:
                code = path_obj.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning("Failed reading file %s: %s", rel_path, e)
                continue

            extracted_symbols, extracted_calls = self._extract_symbols_and_calls(rel_path, code)

            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM code_symbols WHERE file_path = %s;", (rel_path,))
                conn.commit()

            if not extracted_symbols:
                continue

            insert_symbol_sql = """
            INSERT INTO code_symbols (id, file_path, name, symbol_type, start_line, end_line, content)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                content = EXCLUDED.content,
                start_line = EXCLUDED.start_line,
                end_line = EXCLUDED.end_line,
                updated_at = CURRENT_TIMESTAMP;
            """
            symbol_records = [
                (
                    s["id"],
                    s["file_path"],
                    s["name"],
                    s["symbol_type"],
                    s["start_line"],
                    s["end_line"],
                    s["content"],
                )
                for s in extracted_symbols
            ]

            with self._get_db_connection() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_batch(cur, insert_symbol_sql, symbol_records)
                conn.commit()

            contents_to_embed = [
                f"{s['symbol_type']} {s['name']} in {s['file_path']}:\n{s['content']}"
                for s in extracted_symbols
            ]
            embeddings = list(self.embed_model.embed(contents_to_embed))

            qdrant_points: list[PointStruct] = []
            for s, vector in zip(extracted_symbols, embeddings, strict=False):
                point_id = str(uuid.uuid5(MVP_NAMESPACE, s["id"]))
                payload = {
                    "symbol_id": s["id"],
                    "name": s["name"],
                    "file_path": s["file_path"],
                    "symbol_type": s["symbol_type"],
                    "start_line": s["start_line"],
                    "end_line": s["end_line"],
                    "content": s["content"],
                }
                qdrant_points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector.tolist(),
                        payload=payload,
                    )
                )

            self.qdrant_client.upsert(
                collection_name=QDRANT_COLLECTION_NAME,
                points=qdrant_points,
            )

            insert_dep_sql = """
            INSERT INTO code_dependencies (caller_symbol_id, callee_name, callee_symbol_id, file_path)
            VALUES (%s, %s, (
                SELECT id FROM code_symbols WHERE name = %s LIMIT 1
            ), %s);
            """
            dep_records = [
                (c["caller_symbol_id"], c["callee_name"], c["callee_name"], c["file_path"])
                for c in extracted_calls
            ]

            if dep_records:
                with self._get_db_connection() as conn:
                    with conn.cursor() as cur:
                        psycopg2.extras.execute_batch(cur, insert_dep_sql, dep_records)
                    conn.commit()

        logger.info("Indexed %d Python files into PostgreSQL and Qdrant.", len(target_files))

    def _fetch_dependent_callers(
        self, symbol_ids: list[str], max_depth: int = 2
    ) -> dict[str, list[str]]:
        """
        Performs recursive CTE traversal in PostgreSQL to determine callers of symbols.
        """
        if not symbol_ids:
            return {}

        query = """
        WITH RECURSIVE caller_hierarchy AS (
            SELECT
                d.callee_symbol_id AS root_target_id,
                d.caller_symbol_id AS direct_caller_id,
                1 AS depth
            FROM code_dependencies d
            WHERE d.callee_symbol_id = ANY(%s)

            UNION

            SELECT
                ch.root_target_id,
                d.caller_symbol_id,
                ch.depth + 1
            FROM code_dependencies d
            JOIN caller_hierarchy ch ON d.callee_symbol_id = ch.direct_caller_id
            WHERE ch.depth < %s
        )
        SELECT
            ch.root_target_id,
            s.name AS caller_name,
            s.file_path,
            s.start_line
        FROM caller_hierarchy ch
        JOIN code_symbols s ON s.id = ch.direct_caller_id
        GROUP BY ch.root_target_id, s.name, s.file_path, s.start_line;
        """

        result: dict[str, list[str]] = {sid: [] for sid in symbol_ids}
        with self._get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (symbol_ids, max_depth))
                rows = cur.fetchall()
                for root_id, caller_name, file_path, start_line in rows:
                    caller_descriptor = f"{caller_name} ({file_path}:{start_line})"
                    if caller_descriptor not in result[root_id]:
                        result[root_id].append(caller_descriptor)

        return result

    def query_semantic_sources(
        self,
        issue_description: str,
        top_k: int = 5,
        max_caller_depth: int = 2,
    ) -> dict[str, Any]:
        """
        Retrieves relevant symbols via Qdrant query_points and maps their blast radius.
        """
        query_embedding = list(self.embed_model.embed([issue_description]))[0]

        search_response = self.qdrant_client.query_points(
            collection_name=QDRANT_COLLECTION_NAME,
            query=query_embedding.tolist(),
            limit=top_k,
        )

        matched_symbols: list[dict[str, Any]] = []
        symbol_ids: list[str] = []

        for hit in search_response.points:
            payload = hit.payload or {}
            sid = payload.get("symbol_id")
            if sid:
                sid_str = str(sid)
                symbol_ids.append(sid_str)
                matched_symbols.append(
                    {
                        "symbol_id": sid_str,
                        "name": payload.get("name"),
                        "file_path": payload.get("file_path"),
                        "symbol_type": payload.get("symbol_type"),
                        "start_line": payload.get("start_line"),
                        "end_line": payload.get("end_line"),
                        "content": payload.get("content"),
                        "similarity_score": hit.score,
                    }
                )

        blast_radius = self._fetch_dependent_callers(symbol_ids, max_depth=max_caller_depth)

        for sym in matched_symbols:
            sym["dependent_callers"] = blast_radius.get(sym["symbol_id"], [])

        return {
            "query": issue_description,
            "semantic_sources": matched_symbols,
        }

    def get_symbol_blast_radius(self, symbol_name: str, max_depth: int = 3) -> dict[str, Any]:
        """
        Computes blast radius and dependent callers for a given symbol name.
        """
        query = "SELECT id, file_path, symbol_type, start_line, end_line FROM code_symbols WHERE name = %s;"
        symbols: list[dict[str, Any]] = []
        with self._get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (symbol_name,))
                for sid, file_path, symbol_type, start_line, end_line in cur.fetchall():
                    symbols.append(
                        {
                            "symbol_id": sid,
                            "file_path": file_path,
                            "symbol_type": symbol_type,
                            "start_line": start_line,
                            "end_line": end_line,
                        }
                    )

        if not symbols:
            return {
                "symbol_name": symbol_name,
                "found": False,
                "callers": [],
            }

        symbol_ids = [s["symbol_id"] for s in symbols]
        blast_map = self._fetch_dependent_callers(symbol_ids, max_depth=max_depth)

        combined_callers: list[str] = []
        for sid in symbol_ids:
            for caller in blast_map.get(sid, []):
                if caller not in combined_callers:
                    combined_callers.append(caller)

        return {
            "symbol_name": symbol_name,
            "found": True,
            "definitions": symbols,
            "dependent_callers": combined_callers,
        }
