from __future__ import annotations

import os
import secrets
import stat
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
        self.workspace_path: Path = workspace_path.resolve()
        self.ticket_id: str = ticket_id
        env_timeout: int = int(os.environ.get("SANDBOX_TIMEOUT_SEC", "120"))
        self.timeout_sec: int = timeout_sec or env_timeout

    def write_test_file_securely(self, rel_path: str, content: str) -> None:
        target: Path = (self.workspace_path / rel_path).resolve()
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
        self,
        test_file: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        cmd_args: list[str] = ["pytest", "-q", test_file] if test_file else ["pytest", "-q"]
        env_token: str = secrets.token_hex(8)
        env_path: Path = Path(f"/tmp/.mvp_env_{env_token}.tmp")
        flags: int = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        mode: int = stat.S_IRUSR | stat.S_IWUSR
        fd: int = os.open(str(env_path), flags, mode)

        sandbox_image: str = os.environ.get("SANDBOX_IMAGE", "mvp-sandbox:latest")

        try:
            with open(fd, "w", encoding="utf-8") as f:
                if extra_env:
                    for k, v in extra_env.items():
                        f.write(f"{k}={v}\n")

            cmd: list[str] = [
                "docker",
                "run",
                "--rm",
                "--network=none",
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
            proc: subprocess.CompletedProcess[str] = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
            return SandboxExecutionResult(proc.returncode, proc.stdout, proc.stderr, False)
        except subprocess.TimeoutExpired as exc:
            return SandboxExecutionResult(
                -1,
                exc.stdout or "",
                "Hermetic sandbox execution timed out.",
                True,
            )
        finally:
            if env_path.exists():
                env_path.unlink()


__all__ = [
    "PythonHermeticSandbox",
    "SandboxExecutionResult",
]
