from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any

from donovanagent.security.danger import assess_command
from donovanagent.security.permissions import PathPermissions
from donovanagent.tools.base import ToolExecutionContext, ToolResult
from donovanagent.tools.process_registry import process_registry
from donovanagent.utils.platform import get_platform_info
from donovanagent.utils.shell import resolve_shell


_CMD_NOT_FOUND_RE = re.compile(
    r"(?:command not found|not found|not recognized|is not recognized|"
    r"could not find|The term .+ is not recognized|"
    r"did you mean|The system cannot find|No such file or directory)",
    re.IGNORECASE,
)


def _suggest_install(command: str, platform_info: Any) -> str:
    """Generate platform-specific install suggestions for common tools."""
    first_word = command.split()[0] if command.strip() else ""
    if not first_word:
        return ""

    suggestions = {
        "git": {
            "Windows": "winget install Git.Git",
            "Darwin": "brew install git",
            "Linux": "apt install git",
        },
        "node": {
            "Windows": "winget install OpenJS.NodeJS",
            "Darwin": "brew install node",
            "Linux": "apt install nodejs",
        },
        "npm": {
            "Windows": "winget install OpenJS.NodeJS",
            "Darwin": "brew install node",
            "Linux": "apt install npm",
        },
        "python3": {
            "Windows": "winget install Python.Python.3.11",
            "Darwin": "brew install python",
            "Linux": "apt install python3",
        },
        "pip": {
            "Windows": "python -m ensurepip",
            "Darwin": "python3 -m ensurepip",
            "Linux": "apt install python3-pip",
        },
        "pip3": {
            "Windows": "python -m ensurepip",
            "Darwin": "python3 -m ensurepip",
            "Linux": "apt install python3-pip",
        },
        "make": {
            "Windows": "winget install GnuWin32.Make",
            "Darwin": "xcode-select --install",
            "Linux": "apt install build-essential",
        },
        "gcc": {
            "Windows": "winget install LLVM.LLVM",
            "Darwin": "xcode-select --install",
            "Linux": "apt install build-essential",
        },
        "rustc": {
            "Windows": "winget install Rustlang.Rustup",
            "Darwin": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
            "Linux": "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh",
        },
        "go": {
            "Windows": "winget install GoLang.Go",
            "Darwin": "brew install go",
            "Linux": "apt install golang",
        },
        "docker": {
            "Windows": "winget install Docker.DockerDesktop",
            "Darwin": "brew install --cask docker",
            "Linux": "apt install docker.io",
        },
        "rg": {
            "Windows": "winget install BurntSushi.ripgrep.MSVC",
            "Darwin": "brew install ripgrep",
            "Linux": "apt install ripgrep",
        },
        "jq": {
            "Windows": "winget install stedolan.jq",
            "Darwin": "brew install jq",
            "Linux": "apt install jq",
        },
        "curl": {
            "Windows": "",
            "Darwin": "",
            "Linux": "apt install curl",
        },
        "wget": {
            "Windows": "",
            "Darwin": "brew install wget",
            "Linux": "apt install wget",
        },
    }

    tool_map = suggestions.get(first_word)
    if tool_map is None:
        # Try pip packages
        return ""

    os_key = platform_info.system
    suggestion = tool_map.get(os_key) or tool_map.get("Linux", "")
    if suggestion:
        return f"\nHint: Install with: {suggestion}"
    return ""


def run_shell(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    command = str(args["command"])
    timeout = int(args.get("timeout_seconds") or ctx.config.tools.terminal.timeout_seconds)
    cwd = PathPermissions(ctx.config).require_cwd(args.get("cwd"))
    extra_env = args.get("env") if isinstance(args.get("env"), dict) else {}
    shell = resolve_shell()
    started = datetime.now(timezone.utc).isoformat()
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in extra_env.items()})
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            shell.args_for(command),
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        process_registry.register(proc.pid, command, str(cwd))
        stdout, stderr = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
            stdout, stderr = proc.communicate()
            process_registry.unregister(proc.pid)
        ctx.db.add_audit(
            "run_shell",
            "agent",
            session_id=ctx.session_id,
            command=command,
            risk_level=assess_command(command).risk,
            approved=True,
            details={"cwd": str(cwd), "timeout": timeout, "timed_out": True},
        )
        return ToolResult(
            False,
            f"Command timed out after {timeout}s\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            {"stdout": stdout, "stderr": stderr, "timed_out": True, "shell": shell.kind},
            exit_code=None,
        )
    except OSError as exc:
        return ToolResult(False, f"Could not start command: {exc}", {"shell": shell.kind})
    finally:
        if proc is not None:
            process_registry.unregister(proc.pid)

    finished = datetime.now(timezone.utc).isoformat()
    assessment = assess_command(command)
    ctx.db.add_audit(
        "run_shell",
        "agent",
        session_id=ctx.session_id,
        command=command,
        risk_level=assessment.risk,
        approved=True,
        details={"cwd": str(cwd), "shell": shell.kind, "started_at": started, "finished_at": finished},
    )

    combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
    # Auto-fix: detect command-not-found and suggest install
    install_hint = ""
    if code != 0 and _CMD_NOT_FOUND_RE.search(combined):
        platform_info = get_platform_info()
        install_hint = _suggest_install(command, platform_info)

    content = f"$ {command}\n\nExit code: {code}\n\n{combined}{install_hint}"
    return ToolResult(
        code == 0,
        content,
        {"stdout": stdout, "stderr": stderr, "cwd": str(cwd), "shell": shell.kind},
        exit_code=code,
    )


def process_status(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    pid = int(args["pid"])
    status = process_registry.status(pid)
    return ToolResult(True, str(status), status)


def kill_process(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    pid = int(args["pid"])
    result = process_registry.kill(pid)
    ctx.db.add_audit(
        "kill_process",
        "agent",
        session_id=ctx.session_id,
        command=f"kill {pid}",
        risk_level="high",
        approved=True,
        details=result,
    )
    return ToolResult(True, f"Terminated PID {pid}", result)
