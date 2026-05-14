from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from donovanagent.tools.base import ToolDefinition, ToolExecutionContext, ToolResult


def _make_handler(command_template: str, timeout: int) -> Any:
    def handler(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        try:
            cmd = command_template.format(**{k: shlex.quote(str(v)) for k, v in args.items()})
        except KeyError as exc:
            return ToolResult(False, f"Missing parameter: {exc}")
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(ctx.config.app.default_workspace),
            )
            output = (result.stdout or "") + (result.stderr or "")
            return ToolResult(result.returncode == 0, output.strip() or "(no output)", exit_code=result.returncode)
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Command timed out after {timeout}s")
        except Exception as exc:
            return ToolResult(False, str(exc))
    return handler


def load_user_tools(config_dir: Path, workspace: str) -> list[ToolDefinition]:
    dirs = [
        config_dir / "tools",
        Path(workspace) / ".DonovanAgent" / "tools",
    ]
    tools: list[ToolDefinition] = []
    seen: set[str] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = str(data.get("name") or f.stem)
            if not name or name in seen:
                continue
            seen.add(name)
            description = str(data.get("description") or "User-defined tool")
            command_template = str(data.get("command") or "")
            if not command_template:
                continue
            timeout = int(data.get("timeout_seconds") or 30)
            risk = str(data.get("risk") or "medium")
            requires_approval = bool(data.get("requires_approval", risk in {"medium", "high"}))
            raw_params = data.get("parameters") or {}
            properties = {k: {"type": v.get("type", "string"), "description": v.get("description", "")} for k, v in raw_params.items()}
            parameters = {"type": "object", "properties": properties, "required": list(raw_params.keys())}
            tools.append(ToolDefinition(
                name=name,
                description=description,
                enabled_key="filesystem.enabled",  # always enabled when filesystem tools are on
                requires_approval=requires_approval,
                risk=risk,
                parameters=parameters,
                handler=_make_handler(command_template, timeout),
            ))
    return tools
