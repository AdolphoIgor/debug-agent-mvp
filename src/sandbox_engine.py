import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SandboxExecutionResult:
    """
    Encapsulates results from sandbox execution runs.
    """

    def __init__(self, exit_code: int, stdout: str, stderr: str):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class SandboxEngine:
    """
    Manages isolated containerized test runs under strict hermetic settings.
    """

    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path
        self.sandbox_image = os.getenv("SANDBOX_IMAGE", "mvp-sandbox:latest")
        self.timeout_sec = int(os.getenv("SANDBOX_TIMEOUT_SEC", "120"))

    def clean_workspace(self) -> None:
        """
        Cleans untracked files and resets working tree.
        """
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=self.workspace_path,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=self.workspace_path,
            capture_output=True,
            check=False,
        )

    def run_tests(
        self,
        test_path: str,
        patch_content: str | None = None,
        run_full_suite: bool = False,
    ) -> SandboxExecutionResult:
        """
        Executes pytest inside the hermetic container with text decoding enforced.
        """
        self.clean_workspace()

        if patch_content and patch_content.strip():
            patch_file = self.workspace_path / ".engine_exec.patch"
            try:
                patch_file.write_text(patch_content, encoding="utf-8")
                apply_res = subprocess.run(
                    ["git", "apply", "--whitespace=nowarn", str(patch_file)],
                    cwd=self.workspace_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if apply_res.returncode != 0:
                    return SandboxExecutionResult(
                        exit_code=1,
                        stdout="",
                        stderr=f"Failed applying patch:\n{apply_res.stderr}",
                    )
            finally:
                if patch_file.exists():
                    patch_file.unlink()

        target_test_file = (self.workspace_path / test_path).resolve()
        if not target_test_file.is_file() or not target_test_file.is_relative_to(
            self.workspace_path
        ):
            return SandboxExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Test file not found: {test_path}",
            )

        test_rel_path = str(target_test_file.relative_to(self.workspace_path))
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-u",
            "1000:1000",
            "-v",
            f"{self.workspace_path}:/workspace",
            "-w",
            "/workspace",
            "-e",
            "PYTHONPATH=/workspace:/workspace/src",
            self.sandbox_image,
            "pytest",
            "-q",
        ]

        if run_full_suite:
            cmd.extend([test_rel_path, "tests"])
        else:
            cmd.append(test_rel_path)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
            return SandboxExecutionResult(
                exit_code=res.returncode,
                stdout=res.stdout or "",
                stderr=res.stderr or "",
            )
        except subprocess.TimeoutExpired:
            return SandboxExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Sandbox execution timed out after {self.timeout_sec} seconds.",
            )
        except Exception as exc:
            return SandboxExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Sandbox error: {exc}",
            )
