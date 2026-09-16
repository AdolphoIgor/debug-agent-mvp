# sandbox_engine.py
from __future__ import annotations
import os
import secrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from db_pool import DatabasePool

@dataclass(frozen=True)
class SandboxExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

class PythonCoWSandbox:
    def __init__(self, workspace_path: Path, ticket_id: str, timeout_sec: int = 120) -> None:
        self.workspace_path = workspace_path.resolve()
        self.ticket_id = ticket_id
        self.timeout_sec = timeout_sec
        # Banco CoW exclusivo para execucao da sandbox
        self.db_name = f"run_sandbox_python_{secrets.token_hex(4)}"
        self.db_provisioned = False

    def provision_cow_database(self) -> Dict[str, str]:
        with DatabasePool.get_connection() as conn:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{self.db_name}" TEMPLATE "client_baseline_db";')
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
        self.db_provisioned = True
        return {
            "DB_NAME": self.db_name,
            "DB_HOST": os.environ.get("POSTGRES_HOST", "postgres"),
            "DB_USER": os.environ.get("POSTGRES_USER", "mvp_user"),
            "DB_PASS": os.environ.get("POSTGRES_PASSWORD", "mvp_password"),
        }

    def teardown_cow_database(self) -> None:
        if not self.db_provisioned:
            return
        with DatabasePool.get_connection() as conn:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s;", (self.db_name,))
                cur.execute(f'DROP DATABASE IF EXISTS "{self.db_name}";')
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)

    def write_test_file_securely(self, rel_path: str, content: str) -> None:
        target = (self.workspace_path / rel_path).resolve()
        if not target.is_relative_to(self.workspace_path):
            raise PermissionError(f"Escape de path detectado: {rel_path}")
        if ".git" in target.parts or target.name.startswith(".git"):
            raise PermissionError("Gravacao no repositorio interno .git expressamente vedada.")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def run_pytest(self, test_file: Optional[str] = None, extra_env: Optional[Dict[str, str]] = None) -> SandboxExecutionResult:
        cmd_args = ["pytest", "-q", test_file] if test_file else ["pytest", "-q"]
        env_token = secrets.token_hex(8)
        env_path = Path(f"/tmp/.mvp_env_{env_token}.tmp")
        fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)

        try:
            with open(fd, "w", encoding="utf-8") as f:
                if extra_env:
                    for k, v in extra_env.items():
                        f.write(f"{k}={v}\n")
                proxy_url = os.environ.get("MVP_RECORD_PLAY_PROXY_URL", "http://record_proxy:8080")
                f.write(f"HTTP_PROXY={proxy_url}\nHTTPS_PROXY={proxy_url}\n")

            cmd = [
                "docker", "run", "--rm",
                "--network=mvp_global_net", # Rede unica compartilhada
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                f"--env-file={str(env_path)}",
                "-v", f"{self.workspace_path}:/workspace:rw",
                "-w", "/workspace",
                "python:3.11-slim",
                *cmd_args
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_sec, check=False)
            return SandboxExecutionResult(proc.returncode, proc.stdout, proc.stderr, False)
        except subprocess.TimeoutExpired as exc:
            return SandboxExecutionResult(-1, exc.stdout or "", "Timeout na execucao da sandbox.", True)
        finally:
            if env_path.exists():
                env_path.unlink()