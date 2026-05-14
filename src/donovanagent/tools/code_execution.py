from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from donovanagent.config.paths import get_paths
from donovanagent.tools.base import ToolExecutionContext, ToolResult


def execute_python(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    code = str(args["code"])
    timeout = int(args.get("timeout_seconds") or ctx.config.tools.code_execution.timeout_seconds)
    temp_dir = get_paths().temp_dir
    temp_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    script = temp_dir / f"DonovanAgent_exec_{stamp}.py"
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=ctx.config.app.default_workspace,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        ctx.db.add_audit(
            "execute",
            "agent",
            session_id=ctx.session_id,
            command=str(script),
            risk_level="medium",
            approved=True,
            details={"timeout": timeout, "timed_out": True},
        )
        return ToolResult(
            False,
            f"Python timed out after {timeout}s\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}",
            {"script": str(script), "stdout": stdout, "stderr": stderr, "timed_out": True},
        )
    ctx.db.add_audit(
        "execute",
        "agent",
        session_id=ctx.session_id,
        command=str(script),
        risk_level="medium",
        approved=True,
        details={"script": str(script)},
    )
    content = f"Script: {script}\nExit code: {proc.returncode}\n\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    return ToolResult(
        proc.returncode == 0,
        content,
        {"script": str(script), "stdout": proc.stdout, "stderr": proc.stderr},
        exit_code=proc.returncode,
    )
