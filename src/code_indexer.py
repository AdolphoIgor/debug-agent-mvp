from __future__ import annotations
import hashlib
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tree_sitter_languages
from fastembed import TextEmbedding
from psycopg2.extras import execute_values
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from db_pool import DatabasePool

MVP_NAMESPACE = uuid.UUID("e6a57005-720d-40c2-b5e0-7ceae1fa7d88")

class EmbeddingModelSingleton:
    _instance: Optional[TextEmbedding] = None
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
    calls: List[str]

class PythonStructuralIndexer:
    def __init__(self, qdrant_url: str = "http://localhost:6333") -> None:
        self.qdrant = QdrantClient(url=qdrant_url)
        self.embed_model = EmbeddingModelSingleton.get_model()
        self.parser = tree_sitter_languages.get_parser("python")
        self._init_qdrant_collection()

    def _init_qdrant_collection(self) -> None:
        collections = [c.name for c in self.qdrant.get_collections().collections]
        if "mvp_codebase" not in collections:
            self.qdrant.create_collection(
                collection_name="mvp_codebase",
                vectors_config=qmodels.VectorParams(size=384, distance=qmodels.Distance.COSINE)
            )
            self.qdrant.create_payload_index(collection_name="mvp_codebase", field_name="project_id", field_schema=qmodels.PayloadSchemaType.KEYWORD)

    def parse_file(self, project_id: str, repo_dir: Path, rel_path: str) -> List[ExtractedSymbol]:
        full_path = repo_dir / rel_path
        if full_path.suffix.lower() != ".py" or not full_path.exists():
            return []

        source_bytes = full_path.read_bytes()
        tree = self.parser.parse(source_bytes)
        symbols: List[ExtractedSymbol] = []
        stack: List[Tuple[Any, str]] = [(tree.root_node, "")]

        while stack:
            node, current_scope = stack.pop()
            is_symbol = False
            symbol_name = ""
            new_scope = current_scope

            if node.type == "class_definition":
                name_child = node.child_by_field_name("name")
                if name_child:
                    symbol_name = source_bytes[name_child.start_byte:name_child.end_byte].decode("utf-8", errors="ignore")
                    new_scope = f"{current_scope}.{symbol_name}" if current_scope else symbol_name
                    is_symbol = True
            elif node.type == "function_definition":
                name_child = node.child_by_field_name("name")
                if name_child:
                    symbol_name = source_bytes[name_child.start_byte:name_child.end_byte].decode("utf-8", errors="ignore")
                    is_symbol = True

            if is_symbol:
                calls = self._extract_calls(node, source_bytes)
                content = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
                signature_hash = hashlib.sha256(content[:128].encode("utf-8")).hexdigest()[:16]
                symbol_id = f"{project_id}::{rel_path}::{current_scope}::{symbol_name}::{signature_hash}"
                symbols.append(
                    ExtractedSymbol(
                        id=symbol_id, name=symbol_name, symbol_type=node.type,
                        file_path=rel_path, scope_path=current_scope,
                        signature_hash=signature_hash, start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1, content=content, calls=calls
                    )
                )

            for child in reversed(node.children):
                stack.append((child, new_scope))

        return symbols

    def _extract_calls(self, parent_node: Any, source_bytes: bytes) -> List[str]:
        calls: List[str] = []
        node_stack = [parent_node]
        while node_stack:
            curr = node_stack.pop()
            if curr.type == "call":
                fn_child = curr.child_by_field_name("function")
                if fn_child:
                    calls.append(source_bytes[fn_child.start_byte:fn_child.end_byte].decode("utf-8", errors="ignore"))
            for c in reversed(curr.children):
                node_stack.append(c)
        return calls

    def query_semantic_bug_sources(self, project_id: str, bug_description: str, top_k: int = 5) -> Dict[str, Any]:
        """
        Gatilho RAG: Busca no banco vetorial as fontes provaveis do erro relatado e resolve o blast radius.
        """
        query_vec = list(self.embed_model.embed([bug_description]))[0].tolist()
        hits = self.qdrant.search(
            collection_name="mvp_codebase",
            query_vector=query_vec,
            query_filter=qmodels.Filter(must=[qmodels.FieldCondition(key="project_id", match=qmodels.MatchValue(value=project_id))]),
            limit=top_k
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
                        (sym_id, project_id, project_id, project_id)
                    )
                    callers = [f"{row[2]} ({row[1]})" for row in cur.fetchall()]
                    results.append({
                        "file_path": payload.get("file_path"),
                        "name": payload.get("name"),
                        "content": payload.get("content"),
                        "dependent_callers": callers
                    })

        return {"semantic_sources": results}