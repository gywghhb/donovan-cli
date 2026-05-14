from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psutil


@dataclass
class ProcessRecord:
    pid: int
    command: str
    cwd: str
    started_at: str


class ProcessRegistry:
    def __init__(self) -> None:
        self._records: dict[int, ProcessRecord] = {}

    def register(self, pid: int, command: str, cwd: str) -> None:
        self._records[pid] = ProcessRecord(
            pid=pid,
            command=command,
            cwd=cwd,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def unregister(self, pid: int) -> None:
        self._records.pop(pid, None)

    def status(self, pid: int) -> dict[str, Any]:
        if not psutil.pid_exists(pid):
            return {"pid": pid, "exists": False}
        proc = psutil.Process(pid)
        with proc.oneshot():
            return {
                "pid": pid,
                "exists": True,
                "name": proc.name(),
                "status": proc.status(),
                "cwd": safe_call(proc.cwd),
                "cmdline": safe_call(proc.cmdline),
                "username": safe_call(proc.username),
                "create_time": proc.create_time(),
                "tracked": pid in self._records,
            }

    def kill(self, pid: int, timeout: float = 5.0) -> dict[str, Any]:
        if pid == os.getpid():
            raise ValueError("DonovanAgent will not terminate its own process")
        if not psutil.pid_exists(pid):
            return {"pid": pid, "terminated": True, "details": "process does not exist"}
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout)
        self.unregister(pid)
        return {"pid": pid, "terminated": True}


def safe_call(func: Any) -> Any:
    try:
        return func()
    except (psutil.Error, OSError):
        return None


process_registry = ProcessRegistry()
