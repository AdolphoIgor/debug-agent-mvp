from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class PythonHermeticSandbox:
    def __init__(
        self,
        workspace_path: Path,
        ticket_id: str,
        timeout_sec: int | None = None,
    ) -> None:
        self.workspace_path = workspace_path.resolve()
        self.ticket_id = ticket_id
        env_timeout = int(os.environ.get("SANDBOX_TIMEOUT_SEC", "120"))
        self.timeout_sec = timeout_sec or env_timeout

    def write_test_file_securely(self, rel_path: str, content: str) -> None:
        target = (self.workspace_path / rel_path).resolve()
        if not target.is_relative_to(self.workspace_path):
            raise PermissionError(f"Path traversal attempt detected: {rel_path}")
        if ".git" in target.parts or target.name.startswith(".git"):
            raise PermissionError("Modifications to the .git directory are prohibited.")

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
        self,
        test_file: str | None = None,
    ) -> SandboxExecutionResult:
        cmd_args = ["pytest", "-q", test_file] if test_file else ["pytest", "-q"]
        sandbox_image = os.environ.get("SANDBOX_IMAGE", "mvp-sandbox:latest")

        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "-v",
            f"{self.workspace_path}:/workspace:rw",
            "-w",
            "/workspace",
            sandbox_image,
            *cmd_args,
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
            return SandboxExecutionResult(
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_str = (
                exc.stdout.decode("utf-8") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            )
            return SandboxExecutionResult(
                returncode=-1,
                stdout=stdout_str,
                stderr="Execution terminated: sandbox execution exceeded configured timeout.",
                timed_out=True,
            )
        except OSError as exc:
            return SandboxExecutionResult(
                returncode=-1,
                stdout="",
                stderr=f"Container execution failure: {str(exc)}",
                timed_out=False,
            )
