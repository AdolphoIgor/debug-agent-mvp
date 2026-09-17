from __future__ import annotations

import ctypes
import hashlib
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tree_sitter_languages
from fastembed import TextEmbedding
from psycopg2.extras import execute_values
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from tree_sitter import Language, Parser

from db_pool import DatabasePool

MVP_NAMESPACE = uuid.UUID("e6a57005-720d-40c2-b5e0-7ceae1fa7d88")


class EmbeddingModelSingleton:
    _instance: TextEmbedding | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_model(cls) -> TextEmbedding:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        return cls._instance


@dataclass(frozen=True)
class ExtractedSymbol:
    id: str
    name: str
    symbol_type: str
    file_path: str
    scope_path: str
    signature_hash: str
    start_line: int
    end_line: int
    content: str
    calls: list[str]


def _load_python_language() -> Any:
    try:
        return tree_sitter_languages.get_language("python")
    except TypeError:
        ext = "dll" if sys.platform == "win32" else ("dylib" if sys.platform == "darwin" else "so")
        pkg_dir = Path(tree_sitter_languages.__file__).parent
        lib_path = pkg_dir / f"languages.{ext}"
        if not lib_path.exists():
            candidates = list(pkg_dir.glob("languages.*"))
            if candidates:
                lib_path = candidates[0]
            else:
                raise RuntimeError(
                    f"Compiled tree-sitter shared library could not be located in {pkg_dir}."
                )

        cdll = ctypes.CDLL(str(lib_path))
        func = cdll.tree_sitter_python
        func.restype = ctypes.c_void_p
        func.argtypes = []
        lang_ptr = func()
        return Language(lang_ptr)


def _init_python_parser(python_language: Any) -> Parser:
    try:
        parser = Parser(python_language)
    except (TypeError, ValueError):
        parser = Parser()

    if getattr(parser, "language", None) != python_language:
        try:
            parser.language = python_language
        except (AttributeError, TypeError):
            if hasattr(parser, "set_language"):
                parser.set_language(python_language)

    return parser


class PythonStructuralIndexer:
    def __init__(self, qdrant_url: str | None = None) -> None:
        target_url = qdrant_url or os.environ.get("QDRANT_URL", "http://qdrant:6333")
        self.qdrant = QdrantClient(url=target_url)
        self.embed_model = EmbeddingModelSingleton.get_model()
        self.language = _load_python_language()
        self.parser = _init_python_parser(self.language)
        self._init_qdrant_collection()

    def _init_qdrant_collection(self) -> None:
        collections = [c.name for c in self.qdrant.get_collections().collections]
        if "mvp_codebase" not in collections:
            self.qdrant.create_collection(
                collection_name="mvp_codebase",
                vectors_config=qmodels.VectorParams(
                    size=384,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            self.qdrant.create_payload_index(
                collection_name="mvp_codebase",
                field_name="project_id",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )
            self.qdrant.create_payload_index(
                collection_name="mvp_codebase",
                field_name="file_path",
                field_schema=qmodels.PayloadSchemaType.KEYWORD,
            )

    def parse_file(self, project_id: str, repo_dir: Path, rel_path: str) -> list[ExtractedSymbol]:
        full_path = repo_dir / rel_path
        if full_path.suffix.lower() != ".py" or not full_path.exists():
            return []

        source_bytes = full_path.read_bytes()
        tree = self.parser.parse(source_bytes)
        symbols: list[ExtractedSymbol] = []
        stack: list[tuple[Any, str]] = [(tree.root_node, "")]

        while stack:
            node, current_scope = stack.pop()
            is_symbol = False
            symbol_name = ""
            new_scope = current_scope

            if node.type == "class_definition":
                name_child = node.child_by_field_name("name")
                if name_child:
                    symbol_name = source_bytes[name_child.start_byte : name_child.end_byte].decode(
                        "utf-8", errors="ignore"
                    )
                    new_scope = f"{current_scope}.{symbol_name}" if current_scope else symbol_name
                    is_symbol = True
            elif node.type == "function_definition":
                name_child = node.child_by_field_name("name")
                if name_child:
                    symbol_name = source_bytes[name_child.start_byte : name_child.end_byte].decode(
                        "utf-8", errors="ignore"
                    )
                    is_symbol = True

            if is_symbol:
                calls = self._extract_calls(node, source_bytes)
                content = source_bytes[node.start_byte : node.end_byte].decode(
                    "utf-8", errors="ignore"
                )
                signature_hash = hashlib.sha256(content[:128].encode("utf-8")).hexdigest()[:16]
                symbol_id = (
                    f"{project_id}::{rel_path}::{current_scope}::{symbol_name}::{signature_hash}"
                )
                symbols.append(
                    ExtractedSymbol(
                        id=symbol_id,
                        name=symbol_name,
                        symbol_type=node.type,
                        file_path=rel_path,
                        scope_path=current_scope,
                        signature_hash=signature_hash,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        content=content,
                        calls=calls,
                    )
                )

            for child in reversed(node.children):
                stack.append((child, new_scope))

        return symbols

    def _extract_calls(self, parent_node: Any, source_bytes: bytes) -> list[str]:
        calls: list[str] = []
        node_stack = [parent_node]
        while node_stack:
            curr = node_stack.pop()
            if curr.type == "call":
                fn_child = curr.child_by_field_name("function")
                if fn_child:
                    calls.append(
                        source_bytes[fn_child.start_byte : fn_child.end_byte].decode(
                            "utf-8", errors="ignore"
                        )
                    )
            for c in reversed(curr.children):
                node_stack.append(c)
        return calls

    def sync_project_files(self, project_id: str, repo_dir: Path, files_to_sync: list[str]) -> None:
        if not files_to_sync:
            return

        all_symbols: list[ExtractedSymbol] = []
        for rel_path in files_to_sync:
            all_symbols.extend(self.parse_file(project_id, repo_dir, rel_path))

        for rel_path in files_to_sync:
            self.qdrant.delete(
                collection_name="mvp_codebase",
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="project_id",
                                match=qmodels.MatchValue(value=project_id),
                            ),
                            qmodels.FieldCondition(
                                key="file_path",
                                match=qmodels.MatchValue(value=rel_path),
                            ),
                        ]
                    )
                ),
            )

        with DatabasePool.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM code_dependencies
                    WHERE project_id = %s
                      AND (
                          caller_symbol_id IN (
                              SELECT id FROM code_symbols WHERE project_id = %s AND file_path = ANY(%s)
                          )
                          OR callee_symbol_id IN (
                              SELECT id FROM code_symbols WHERE project_id = %s AND file_path = ANY(%s)
                          )
                      );
                    """,
                    (project_id, project_id, files_to_sync, project_id, files_to_sync),
                )

                cur.execute(
                    "DELETE FROM code_symbols WHERE project_id = %s AND file_path = ANY(%s);",
                    (project_id, files_to_sync),
                )

                if all_symbols:
                    symbol_rows = [
                        (
                            s.id,
                            project_id,
                            s.file_path,
                            s.name,
                            s.symbol_type,
                            s.scope_path,
                            s.signature_hash,
                            s.start_line,
                            s.end_line,
                            hashlib.sha256(s.content.encode("utf-8")).hexdigest(),
                        )
                        for s in all_symbols
                    ]
                    execute_values(
                        cur,
                        """
                        INSERT INTO code_symbols (
                            id, project_id, file_path, symbol_name, symbol_type,
                            scope_path, signature_hash, start_line, end_line, content_hash
                        ) VALUES %s
                        ON CONFLICT (id) DO UPDATE SET
                            symbol_type = EXCLUDED.symbol_type,
                            scope_path = EXCLUDED.scope_path,
                            start_line = EXCLUDED.start_line,
                            end_line = EXCLUDED.end_line,
                            content_hash = EXCLUDED.content_hash,
                            updated_at = CURRENT_TIMESTAMP;
                        """,
                        symbol_rows,
                    )

                    caller_ids: list[str] = []
                    callee_names: list[str] = []
                    caller_files: list[str] = []
                    proj_ids: list[str] = []

                    for s in all_symbols:
                        for called_name in s.calls:
                            caller_ids.append(str(s.id))
                            callee_names.append(str(called_name))
                            caller_files.append(str(s.file_path))
                            proj_ids.append(str(project_id))

                    if caller_ids:
                        cur.execute(
                            """
                            INSERT INTO code_dependencies (caller_symbol_id, callee_symbol_id, project_id)
                            SELECT DISTINCT
                                c.caller_id,
                                cs.id,
                                c.proj_id
                            FROM (
                                SELECT
                                    v.caller_id,
                                    v.callee_name,
                                    v.caller_file,
                                    v.proj_id
                                FROM UNNEST(%s::text[], %s::text[], %s::text[], %s::text[])
                                AS v(caller_id, callee_name, caller_file, proj_id)
                            ) c
                            JOIN code_symbols cs
                              ON cs.project_id = c.proj_id
                             AND (
                                  (cs.file_path = c.caller_file AND cs.symbol_name = c.callee_name)
                                  OR (
                                      c.callee_name = (
                                          CASE 
                                              WHEN cs.scope_path IS NULL OR cs.scope_path = '' THEN cs.symbol_name 
                                              ELSE cs.scope_path || '.' || cs.symbol_name 
                                          END
                                      )
                                      AND POSITION('.' IN c.callee_name) > 0
                                  )
                             )
                            ON CONFLICT DO NOTHING;
                            """,
                            (caller_ids, callee_names, caller_files, proj_ids),
                        )
            conn.commit()

        if all_symbols:
            texts = [
                f"Symbol: {s.name} ({s.symbol_type}) in {s.file_path}\nCode:\n{s.content}"
                for s in all_symbols
            ]
            vectors = list(self.embed_model.embed(texts))
            points = []
            for s, vec in zip(all_symbols, vectors):
                point_uuid = str(uuid.uuid5(MVP_NAMESPACE, s.id))
                points.append(
                    qmodels.PointStruct(
                        id=point_uuid,
                        vector=vec.tolist(),
                        payload={
                            "symbol_id": s.id,
                            "project_id": project_id,
                            "file_path": s.file_path,
                            "name": s.name,
                            "content": s.content,
                        },
                    )
                )
            self.qdrant.upsert(collection_name="mvp_codebase", points=points)

    def query_semantic_bug_sources(
        self, project_id: str, bug_description: str, top_k: int = 5
    ) -> dict[str, Any]:
        query_vec = list(self.embed_model.embed([bug_description]))[0].tolist()
        hits = self.qdrant.search(
            collection_name="mvp_codebase",
            query_vector=query_vec,
            query_filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="project_id", match=qmodels.MatchValue(value=project_id)
                    )
                ]
            ),
            limit=top_k,
        )

        results = []
        with DatabasePool.get_connection() as conn:
            with conn.cursor() as cur:
                for hit in hits:
                    payload = hit.payload or {}
                    sym_id = payload.get("symbol_id", "")
                    cur.execute(
                        """
                        WITH RECURSIVE callers AS (
                            SELECT caller_symbol_id, 1 as depth
                            FROM code_dependencies
                            WHERE callee_symbol_id = %s AND project_id = %s
                            UNION
                            SELECT cd.caller_symbol_id, c.depth + 1
                            FROM code_dependencies cd
                            JOIN callers c ON cd.callee_symbol_id = c.caller_symbol_id
                            WHERE c.depth < 2 AND cd.project_id = %s
                        )
                        SELECT DISTINCT cs.id, cs.file_path, cs.symbol_name
                        FROM callers cl
                        JOIN code_symbols cs ON cs.id = cl.caller_symbol_id AND cs.project_id = %s;
                        """,
                        (sym_id, project_id, project_id, project_id),
                    )
                    callers = [f"{row[2]} ({row[1]})" for row in cur.fetchall()]
                    results.append(
                        {
                            "file_path": payload.get("file_path"),
                            "name": payload.get("name"),
                            "content": payload.get("content"),
                            "dependent_callers": callers,
                        }
                    )

        return {"semantic_sources": results}
