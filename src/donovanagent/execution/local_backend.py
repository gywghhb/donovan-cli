from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from donovanagent.execution.base import ExecutionBackend
from donovanagent.utils.shell import resolve_shell


class LocalExecutionBackend(ExecutionBackend):
    """Local execution using the resolved shell."""

    @property
    def name(self) -> str:
        return "local"

    def run_command(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
        stream: bool = False,
    ) -> str:
        shell = resolve_shell()
        merged_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(
                shell.args_for(command),
                cwd=cwd or os.getcwd(),
                env=merged_env,
                capture_output=not stream,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}" if stderr else stdout
            return combined
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s"
        except OSError as exc:
            return f"Could not start command: {exc}"

    def read_file(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def list_directory(self, path: str) -> list[dict[str, Any]]:
        entries = []
        p = Path(path)
        if not p.is_dir():
            return entries
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.name.startswith("."):
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "type": "directory" if child.is_dir() else "file",
                    "size": stat.st_size,
                })
            except OSError:
                continue
        return entries

    def path_exists(self, path: str) -> bool:
        return Path(path).exists()

    def get_system_info(self) -> dict[str, str]:
        import platform
        return {
            "os": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        }

    def close(self) -> None:
        pass
