from __future__ import annotations

import os
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
    def __init__(
        self, workspace_path: Path, ticket_id: str, timeout_sec: int | None = None
    ) -> None:
        self.workspace_path = workspace_path.resolve()
        self.ticket_id = ticket_id
        env_timeout = int(os.environ.get("SANDBOX_TIMEOUT_SEC", "120"))
        self.timeout_sec = timeout_sec or env_timeout
        sanitized_id = re.sub(r"[^a-zA-Z0-9_]", "_", ticket_id)
        self.db_name = f"run_sandbox_python_{sanitized_id}_{secrets.token_hex(4)}"
        self.db_provisioned = False

    def provision_cow_database(self) -> dict[str, str]:
        baseline_db = os.environ.get("BASELINE_DB_NAME", "client_baseline_db")
        with DatabasePool.get_connection() as conn:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{self.db_name}" TEMPLATE "{baseline_db}";')
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
        self.db_provisioned = True
        return {
            "DB_NAME": self.db_name,
            "DB_HOST": os.environ.get("POSTGRES_HOST", "postgres"),
            "DB_PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "DB_USER": os.environ.get("POSTGRES_USER", "mvp_user"),
            "DB_PASS": os.environ.get("POSTGRES_PASSWORD", "mvp_password"),
        }

    def teardown_cow_database(self) -> None:
        if not self.db_provisioned:
            return
        with DatabasePool.get_connection() as conn:
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_terminate_backend(pid) 
                    FROM pg_stat_activity 
                    WHERE datname = %s AND pid <> pg_backend_pid();
                    """,
                    (self.db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{self.db_name}";')
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
        self.db_provisioned = False

    def write_test_file_securely(self, rel_path: str, content: str) -> None:
        target = (self.workspace_path / rel_path).resolve()
        if not target.is_relative_to(self.workspace_path):
            raise PermissionError(f"Path traversal escape detected: {rel_path}")
        if ".git" in target.parts or target.name.startswith(".git"):
            raise PermissionError("Writing to the .git directory is strictly prohibited.")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def apply_patch(self, unified_diff: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "apply", "--whitespace=fix", "-"],
            input=unified_diff,
            text=True,
            cwd=str(self.workspace_path),
            capture_output=True,
            check=False,
        )

    def run_pytest(
        self, test_file: str | None = None, extra_env: dict[str, str] | None = None
    ) -> SandboxExecutionResult:
        cmd_args = ["pytest", "-q", test_file] if test_file else ["pytest", "-q"]
        env_token = secrets.token_hex(8)
        env_path = Path(f"/tmp/.mvp_env_{env_token}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        mode = stat.S_IRUSR | stat.S_IWUSR
        fd = os.open(str(env_path), flags, mode)

        network_name = os.environ.get("DOCKER_NETWORK_NAME", "mvp_global_net")
        sandbox_image = os.environ.get("SANDBOX_IMAGE", "mvp-sandbox:latest")

        try:
            with open(fd, "w", encoding="utf-8") as f:
                if extra_env:
                    for k, v in extra_env.items():
                        f.write(f"{k}={v}\n")
                proxy_url = os.environ.get("MVP_RECORD_PLAY_PROXY_URL", "http://record-proxy:8080")
                f.write(f"HTTP_PROXY={proxy_url}\n")
                f.write(f"HTTPS_PROXY={proxy_url}\n")

            cmd = [
                "docker",
                "run",
                "--rm",
                f"--network={network_name}",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                f"--env-file={str(env_path)}",
                "-v",
                f"{self.workspace_path}:/workspace:rw",
                "-w",
                "/workspace",
                sandbox_image,
                *cmd_args,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_sec, check=False
            )
            return SandboxExecutionResult(proc.returncode, proc.stdout, proc.stderr, False)
        except subprocess.TimeoutExpired as exc:
            return SandboxExecutionResult(
                -1, exc.stdout or "", "Sandbox execution timed out.", True
            )
        finally:
            if env_path.exists():
                env_path.unlink()
