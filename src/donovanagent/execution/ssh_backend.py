from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from donovanagent.execution.base import ExecutionBackend
from donovanagent.utils.errors import ProviderError
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class SSHConfigError(Exception):
    """Raised when SSH configuration is invalid."""
    pass


class SSHExecutionBackend(ExecutionBackend):
    """Execute commands on a remote host via SSH using subprocess."""

    def __init__(
        self,
        host: str | None = None,
        port: int = 22,
        username: str | None = None,
        key_path: str | None = None,
        remote_workspace: str | None = None,
    ) -> None:
        if not host:
            raise SSHConfigError("SSH host is required")
        self.host = host
        self.port = port
        self.username = username or os.environ.get("USER", "root")
        self.key_path = key_path
        self.remote_workspace = remote_workspace or "/tmp/DonovanAgent"
        self._check_ssh()

    def _check_ssh(self) -> None:
        import shutil
        if not shutil.which("ssh"):
            raise ProviderError("SSH client not found. Install OpenSSH.")

    @property
    def name(self) -> str:
        return f"ssh:{self.host}"

    def _ssh_args(self, command: str) -> list[str]:
        import shlex
        args = [
            "ssh",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-p", str(self.port),
        ]
        if self.key_path:
            key = Path(self.key_path).expanduser()
            if not key.exists():
                raise SSHConfigError(f"SSH key not found: {self.key_path}")
            args.extend(["-i", str(key)])
        args.append(f"{self.username}@{self.host}")
        args.append(command)
        return args

    def run_command(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
        stream: bool = False,
    ) -> str:
        import subprocess
        remote_cmd = f"cd {cwd or self.remote_workspace} && {command}"
        # Add env vars inline
        env_prefix = ""
        if env:
            env_prefix = " ".join(f"{k}={v}" for k, v in env.items()) + " "
            remote_cmd = f"cd {cwd or self.remote_workspace} && {env_prefix}{command}"

        try:
            proc = subprocess.run(
                self._ssh_args(remote_cmd),
                capture_output=not stream,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stderr else stdout
        except subprocess.TimeoutExpired:
            return f"SSH command timed out after {timeout}s"
        except OSError as exc:
            return f"SSH error: {exc}"

    def read_file(self, path: str) -> str:
        result = self.run_command(f"cat {path}")
        if "STDERR:" in result:
            lines = result.split("\nSTDERR:\n", 1)
            if lines[1].strip():
                raise OSError(f"Could not read file: {lines[1].strip()}")
            return lines[0].replace("STDOUT:\n", "", 1)
        return result

    def write_file(self, path: str, content: str) -> None:
        import tempfile
        import subprocess
        tmp = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        try:
            self.run_command(f"mkdir -p {Path(path).parent}")
            subprocess.run(
                ["scp", "-P", str(self.port), tmp.name, f"{self.username}@{self.host}:{path}"],
                capture_output=True, text=True, timeout=30,
            )
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    def list_directory(self, path: str) -> list[dict[str, Any]]:
        result = self.run_command(
            f"ls -la {path} && echo '---' && find {path} -maxdepth 1 -type f -o -type d | head -100"
        )
        entries = []
        for line in result.splitlines():
            if line.startswith("---"):
                break
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
        os_info = self.run_command("uname -a")
        python = self.run_command("python3 --version 2>/dev/null || echo 'unknown'")
        return {"os": os_info.strip()[:200], "python": python.strip()[:50]}

    def close(self) -> None:
        pass
