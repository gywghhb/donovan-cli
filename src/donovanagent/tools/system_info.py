from __future__ import annotations

import os
import shutil
from typing import Any

import psutil

from donovanagent.tools.base import ToolExecutionContext, ToolResult
from donovanagent.utils.platform import get_platform_info
from donovanagent.utils.shell import resolve_shell


def get_system_info(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    platform_info = get_platform_info()
    shell = resolve_shell()
    data: dict[str, Any] = {
        "os": platform_info.system,
        "release": platform_info.release,
        "machine": platform_info.machine,
        "python": platform_info.python,
        "python_executable": platform_info.executable,
        "encoding": platform_info.encoding,
        "cwd": os.getcwd(),
        "workspace": ctx.config.app.default_workspace,
        "shell": shell.__dict__,
        "cpu_count": os.cpu_count(),
        "memory_total": psutil.virtual_memory().total,
        "tools": {
            "git": shutil.which("git"),
            "rg": shutil.which("rg"),
            "node": shutil.which("node"),
        },
    }
    lines = [f"{key}: {value}" for key, value in data.items()]
    return ToolResult(True, "\n".join(lines), data)
