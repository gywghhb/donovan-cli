from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from donovanagent.execution.base import ExecutionBackend
from donovanagent.utils.errors import ProviderError
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class DockerExecutionBackend(ExecutionBackend):
    """Execute commands inside Docker containers."""

    def __init__(
        self,
        image: str | None = None,
        container: str | None = None,
        mount_workspace: bool = True,
        workspace: str | None = None,
    ) -> None:
        self._image = image or "python:3.11-slim"
        self._container_name = container
        self._mount_workspace = mount_workspace
        self._workspace = workspace or os.getcwd()
        self._check_docker()

    def _check_docker(self) -> None:
        try:
            subprocess.run(
                ["docker", "--version"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise ProviderError(
                "Docker is not available. Install Docker Desktop or the docker CLI."
            )

    @property
    def name(self) -> str:
        if self._container_name:
            return f"docker:{self._container_name}"
        return f"docker:{self._image}"

    def _container_args(self, command: str) -> list[str]:
        if self._container_name:
            return ["docker", "exec", "-w", self._workspace, self._container_name, "sh", "-c", command]

        mounts = []
        if self._mount_workspace:
            mounts.extend(["-v", f"{self._workspace}:{self._workspace}"])
        return [
            "docker", "run", "--rm",
            *mounts,
            "-w", self._workspace,
            self._image,
            "sh", "-c", command,
        ]

    def run_command(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
        stream: bool = False,
    ) -> str:
        args = self._container_args(command)
        merged_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(
                args, cwd=cwd or self._workspace, env=merged_env,
                capture_output=not stream, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stderr else stdout
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s"
        except OSError as exc:
            return f"Docker error: {exc}"

    def read_file(self, path: str) -> str:
        result = self.run_command(f"cat {path}")
        if "STDERR:" in result:
            lines = result.split("\nSTDERR:\n", 1)
            if lines[1].strip():
                raise OSError(f"Could not read file: {lines[1].strip()}")
            return lines[0].replace("STDOUT:\n", "", 1)
        return result

    def write_file(self, path: str, content: str) -> None:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        try:
            remote_dir = str(Path(path).parent)
            self.run_command(f"mkdir -p {remote_dir}")
            self.run_command(f"cp {tmp.name} {path}")
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def list_directory(self, path: str) -> list[dict[str, Any]]:
        result = self.run_command(f"ls -la {path}")
        entries = []
        for line in result.splitlines():
            parts = line.split(None, 8)
            if len(parts) >= 9 and not line.startswith("total"):
                entries.append({
                    "name": parts[8],
                    "path": f"{path}/{parts[8]}",
                    "size": int(parts[4]),
                })
        return entries

    def path_exists(self, path: str) -> bool:
        result = self.run_command(f"test -e {path} && echo 'exists' || echo 'not_found'")
        return "exists" in result

    def get_system_info(self) -> dict[str, str]:
        os_info = self.run_command("cat /etc/os-release 2>/dev/null || uname -a")
        python = self.run_command("python3 --version 2>/dev/null || python --version 2>/dev/null || echo 'unknown'")
        return {"os": os_info.strip()[:100], "python": python.strip()[:50]}

    def close(self) -> None:
        pass
