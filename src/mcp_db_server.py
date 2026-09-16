import os
import uvicorn
from contextlib import contextmanager
from mcp.server.fastmcp import FastMCP
import psycopg2
from psycopg2.extras import RealDictCursor

# MCP Server com nome fixo para o showcase
mcp = FastMCP("MVP-DB-Access")

@contextmanager
def get_db_connection():
    db_url = os.environ.get("TARGET_DB_URL", "postgresql://:mvp_password@localhost:5432/client_baseline_db")
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()

@mcp.tool()
def query_database(sql_query: str) -> str:
    """
    Executa consultas DQL (somente leitura, como SELECT) no banco de dados do cliente 
    para inspecionar esquemas, tabelas ou dados associados ao bug reportado.
    Nao permite operacoes de escrita (INSERT, UPDATE, DELETE).
    """
    upper_query = sql_query.strip().upper()
    if any(forbidden in upper_query for forbidden in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"]):
        return "Erro: Acesso negado. Apenas consultas de leitura (SELECT) sao permitidas via MCP."
    
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql_query)
                results = cur.fetchmany(50)  # Limite rigido para evitar exaustao de tokens
                if not results:
                    return "Consulta executada com sucesso. Nenhum resultado encontrado."
                
                # Formatacao otimizada para consumo do LLM
                lines = [str(dict(row)) for row in results]
                return "\n".join(lines)
    except Exception as e:
        return f"Falha na execucao da consulta SQL: {str(e)}"

if __name__ == "__main__":
    # Roda o servidor MCP via Server-Sent Events (SSE) para conexao local do Orquestrador
    mcp.run(transport='sse', host="0.0.0.0", port=8080)