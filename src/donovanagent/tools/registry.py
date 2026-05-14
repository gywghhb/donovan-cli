from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.security.danger import assess_command
from donovanagent.tools.approval import ApprovalRequest
from donovanagent.tools.base import ToolDefinition, ToolExecutionContext, ToolResult
from donovanagent.tools.browser_control import (
    browser_back,
    browser_click,
    browser_close,
    browser_current_url,
    browser_evaluate,
    browser_extract_links,
    browser_get_html,
    browser_open,
    browser_press,
    browser_reload,
    browser_screenshot,
    browser_snapshot,
    browser_type,
)
from donovanagent.tools.code_execution import execute_python
from donovanagent.tools.filesystem import list_directory, patch_file, read_file, search_files, write_file
from donovanagent.tools.system_info import get_system_info
from donovanagent.tools.terminal import kill_process, process_status, run_shell
from donovanagent.tools.web import web_search
from donovanagent.tools.mcp_tools import CONTROL_TOOL_DEFS


class ToolRegistry:
    def __init__(self, config: DonovanAgentConfig) -> None:
        self.config = config
        self._tools: dict[str, ToolDefinition] = {}
        self._openai_schemas_cache: list[dict[str, Any]] | None = None

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        self.invalidate_schema_cache()

    def unregister(self, name: str) -> None:
        """Remove a tool by name. No-op if tool does not exist."""
        self._tools.pop(name, None)
        self.invalidate_schema_cache()

    def get(self, name: str) -> ToolDefinition:
        return self._tools[name]

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def enabled_tools(self) -> list[ToolDefinition]:
        return [tool for tool in self.list() if self.is_enabled(tool)]

    def openai_schemas(self) -> list[dict[str, Any]]:
        if self._openai_schemas_cache is not None:
            return self._openai_schemas_cache
        result = [tool.openai_schema() for tool in self.enabled_tools()]
        self._openai_schemas_cache = result
        return result

    def invalidate_schema_cache(self) -> None:
        self._openai_schemas_cache = None

    def is_enabled(self, tool: ToolDefinition | str) -> bool:
        if isinstance(tool, str):
            tool = self._tools[tool]
        node: Any = self.config.tools
        for part in tool.enabled_key.split("."):
            node = getattr(node, part)
        if bool(node):
            return True
        # web_search is also enabled when search is configured
        if tool.name == "web_search":
            return self.config.search.enabled
        return False

    def rows(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "enabled": self.is_enabled(tool),
                "requires_approval": tool.requires_approval,
                "risk": tool.risk,
            }
            for tool in self.list()
        ]

    def execute(
        self, ctx: ToolExecutionContext, name: str, args: dict[str, Any], tool_call_id: str | None = None
    ) -> ToolResult:
        if name not in self._tools:
            # Attempt MCP tool name repair for malformed names
            if name.startswith("mcp"):
                from donovanagent.mcp.registry import repair_mcp_tool_name
                repaired = repair_mcp_tool_name(name, set(self._tools.keys()))
                if repaired is not None:
                    name = repaired
                    tool = self._tools[name]
                else:
                    return ToolResult(False, f"Unknown tool: {name}")
            else:
                return ToolResult(False, f"Unknown tool: {name}")
        else:
            tool = self._tools[name]
        if not self.is_enabled(tool):
            return ToolResult(False, f"Tool is disabled: {name}")
        risk = tool.risk
        typed = False
        reasons: list[str] = []
        if name == "run_shell":
            assessment = assess_command(str(args.get("command", "")))
            risk = assessment.risk
            typed = assessment.requires_typed_confirmation
            reasons = assessment.reasons
        requires_approval = self._requires_approval(name, tool, risk)
        approved: bool | None = None
        approval_reason: str | None = None
        if requires_approval:
            body = self._approval_body(name, args, risk, reasons)
            decision = ctx.approval.request(
                ApprovalRequest(
                    title=name,
                    body=body,
                    risk=risk,
                    typed_confirmation=typed,
                )
            )
            approved = decision.approved
            approval_reason = decision.reason
            if not decision.approved:
                ctx.db.add_audit(
                    name,
                    "agent",
                    session_id=ctx.session_id,
                    command=args.get("command"),
                    risk_level=risk,
                    approved=False,
                    details={"reason": decision.reason, "arguments": args},
                )
                result = ToolResult(False, f"Approval denied: {decision.reason}")
                self._persist_call(ctx, name, args, result, tool_call_id, approved, approval_reason)
                return result
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = tool.handler(ctx, args)
        except Exception as exc:  # tools must surface errors as structured results
            result = ToolResult(False, f"{type(exc).__name__}: {exc}")
        finished = datetime.now(timezone.utc).isoformat()
        self._persist_call(
            ctx,
            name,
            args,
            result,
            tool_call_id,
            approved,
            approval_reason,
            started_at=started,
            finished_at=finished,
        )
        return result

    def _requires_approval(self, name: str, tool: ToolDefinition, risk: str) -> bool:
        mode = self.config.app.permission_mode
        # Full autonomy Ã¢â‚¬â€ never ask
        if mode == "full_autonomy":
            return False
        if name in {"write_file", "patch_file"}:
            return False
        if risk == "high" and self.config.security.require_approval_for_destructive_commands:
            return True
        if name == "run_shell":
            return self.config.tools.terminal.require_approval or mode in {"readonly", "review", "workspace"}
        if name == "execute":
            return self.config.tools.code_execution.require_approval
        if name == "kill_process":
            return True
        return tool.requires_approval

    def _approval_body(self, name: str, args: dict[str, Any], risk: str, reasons: list[str]) -> str:
        """Return a concise one-liner Ã¢â‚¬â€ no code dumps, no huge blocks."""
        if name == "run_shell":
            body = f"Command: {str(args.get('command', ''))[:120]}"
        elif name == "execute":
            body = "Execute Python code locally"
        elif name == "kill_process":
            body = f"Terminate process PID {args.get('pid')}"
        elif name in {"write_file", "patch_file"}:
            body = f"Modify file: {args.get('path', '')}"
        else:
            body = str(name)
        if reasons:
            body += "  Ã¢â‚¬â€  " + ", ".join(reasons[:2])
        return body

    def _persist_call(
        self,
        ctx: ToolExecutionContext,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
        tool_call_id: str | None,
        approved: bool | None,
        approval_reason: str | None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        if not ctx.session_id:
            return
        ctx.db.add_tool_call(
            ctx.session_id,
            name,
            args,
            result.to_dict(),
            tool_call_id=tool_call_id,
            exit_code=result.exit_code,
            started_at=started_at,
            finished_at=finished_at,
            approved=approved,
            approval_reason=approval_reason,
        )


# ---------------------------------------------------------------------------
# Subagent tool Ã¢â‚¬â€ lets the AI spawn child agents for parallel sub-tasks
# ---------------------------------------------------------------------------

def spawn_subagent(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Spawn a subagent to work on a sub-task independently.

    Tool parameters:
      - role (str, required): researcher|coder|tester|reviewer|safety|planner|browser_qa|custom
      - goal (str, required): the task description for the subagent
      - wait (bool, default True): if True, blocks until the subagent
        completes and returns its result; if False, returns immediately
        with the subagent ID.
    """
    mgr = ctx.subagent_manager
    if mgr is None:
        return ToolResult(False, "Subagent system is not available in this context.")

    if not mgr.can_spawn:
        return ToolResult(False, "Maximum parallel subagents already running. Try again later.")

    role = str(args.get("role", "")).lower()
    goal = str(args.get("goal", ""))
    wait = bool(args.get("wait", True))

    from donovanagent.subagents.roles import ROLE_PRESETS
    if role not in ROLE_PRESETS:
        valid = ", ".join(sorted(ROLE_PRESETS))
        return ToolResult(False, f"Unknown role '{role}'. Valid roles: {valid}")

    sub = mgr.create_and_start(role, goal)

    if not wait:
        return ToolResult(True, f"Subagent {sub.id} ({sub.name}) started in background. "
                                f"Use /subagents result {sub.id} to check later.")

    # Poll until done
    import time
    while sub.status not in ("completed", "failed"):
        time.sleep(0.5)

    if sub.status == "completed":
        return ToolResult(True, f"[Subagent {sub.name} ({sub.id})]\n{sub.result_summary or '(empty)'}")
    else:
        return ToolResult(False, f"Subagent {sub.id} failed: {sub.error or 'Unknown error'}")


def build_default_registry(config: DonovanAgentConfig) -> ToolRegistry:
    registry = ToolRegistry(config)
    registry.register(
        ToolDefinition(
            name="run_shell",
            description="Run a shell command on the user's operating system.",
            enabled_key="terminal.enabled",
            requires_approval=True,
            risk="medium",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                    "env": {"type": "object", "additionalProperties": {"type": "string"}},
                },
                "required": ["command"],
            },
            handler=run_shell,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file inside approved DonovanAgent paths.",
            enabled_key="filesystem.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "max_bytes": {"type": "integer"},
                },
                "required": ["path"],
            },
            handler=read_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Write a UTF-8 text file inside approved paths, showing a diff and creating a backup.",
            enabled_key="filesystem.enabled",
            requires_approval=True,
            risk="medium",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            handler=write_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="patch_file",
            description="Patch a text file by replacing exact search text with replacement text.",
            enabled_key="filesystem.enabled",
            requires_approval=True,
            risk="medium",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "search": {"type": "string"},
                    "replace": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["path", "search", "replace"],
            },
            handler=patch_file,
        )
    )
    registry.register(
        ToolDefinition(
            name="list_directory",
            description="List files and folders in an approved directory.",
            enabled_key="filesystem.enabled",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "show_hidden": {"type": "boolean"}},
            },
            handler=list_directory,
        )
    )
    registry.register(
        ToolDefinition(
            name="search_files",
            description="Search text in files under an approved folder using ripgrep when available.",
            enabled_key="filesystem.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            handler=search_files,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_system_info",
            description="Inspect OS, Python, shell, and common tool availability.",
            enabled_key="system_info.enabled",
            parameters={"type": "object", "properties": {}},
            handler=get_system_info,
        )
    )
    registry.register(
        ToolDefinition(
            name="web_search",
            description="Search the web using the configured Tavily provider.",
            enabled_key="web_search.enabled",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                "required": ["query"],
            },
            handler=web_search,
        )
    )
    registry.register(
        ToolDefinition(
            name="execute",
            description="Run Python code locally with timeout. This is not a security sandbox.",
            enabled_key="code_execution.enabled",
            requires_approval=True,
            risk="medium",
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string"}, "timeout_seconds": {"type": "integer"}},
                "required": ["code"],
            },
            handler=execute_python,
        )
    )
    registry.register(
        ToolDefinition(
            name="process_status",
            description="Inspect a running process by PID.",
            enabled_key="terminal.enabled",
            parameters={"type": "object", "properties": {"pid": {"type": "integer"}}, "required": ["pid"]},
            handler=process_status,
        )
    )
    registry.register(
        ToolDefinition(
            name="kill_process",
            description="Terminate a running process by PID.",
            enabled_key="terminal.enabled",
            requires_approval=True,
            risk="high",
            parameters={"type": "object", "properties": {"pid": {"type": "integer"}}, "required": ["pid"]},
            handler=kill_process,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_open",
            description="Open a URL in the browser. Launches the browser if not already open. To connect to a browser you already have running, pass cdp_endpoint (e.g. http://localhost:9222) — you must start your browser with --remote-debugging-port=9222 first.",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to navigate to."},
                    "browser_type": {
                        "type": "string",
                        "enum": ["auto", "chromium", "chrome", "edge", "webkit"],
                        "description": "Browser engine to use (default: auto).",
                    },
                    "cdp_endpoint": {
                        "type": "string",
                        "description": "CDP endpoint to connect to an already-running browser (e.g. http://localhost:9222). Requires browser launched with --remote-debugging-port.",
                    },
                },
                "required": ["url"],
            },
            handler=browser_open,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_snapshot",
            description="Get the visible text content of the current browser page.",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "description": "Maximum characters to return (default: 5000)."}
                },
            },
            handler=browser_snapshot,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_screenshot",
            description="Take a screenshot of the current page and return the file path.",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Optional custom file path for the screenshot."}
                },
            },
            handler=browser_screenshot,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_click",
            description="Click an element on the page identified by a CSS selector.",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of the element to click."}
                },
                "required": ["selector"],
            },
            handler=browser_click,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_type",
            description="Type text into an element identified by a CSS selector.",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector of the input element."},
                    "text": {"type": "string", "description": "Text to type into the element."},
                },
                "required": ["selector", "text"],
            },
            handler=browser_type,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_press",
            description="Press a keyboard key (e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown').",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key to press (e.g. 'Enter', 'Tab', 'Escape')."}
                },
                "required": ["key"],
            },
            handler=browser_press,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_extract_links",
            description="Extract all links (text and href) from the current page.",
            enabled_key="browser_tools.enabled",
            parameters={"type": "object", "properties": {}},
            handler=browser_extract_links,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_current_url",
            description="Get the URL of the currently focused browser tab. Only returns one URL — the active tab. Does NOT return a list of all open tabs or pages.",
            enabled_key="browser_tools.enabled",
            parameters={"type": "object", "properties": {}},
            handler=browser_current_url,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_back",
            description="Navigate back in the browser history.",
            enabled_key="browser_tools.enabled",
            parameters={"type": "object", "properties": {}},
            handler=browser_back,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_reload",
            description="Reload the current page.",
            enabled_key="browser_tools.enabled",
            parameters={"type": "object", "properties": {}},
            handler=browser_reload,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_get_html",
            description="Get the full HTML content of the current page.",
            enabled_key="browser_tools.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "description": "Maximum characters to return (default: 10000)."}
                },
            },
            handler=browser_get_html,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_close",
            description="Close the browser and release all resources.",
            enabled_key="browser_tools.enabled",
            parameters={"type": "object", "properties": {}},
            handler=browser_close,
        )
    )
    registry.register(
        ToolDefinition(
            name="browser_evaluate",
            description="Evaluate JavaScript in the current page context and return the result.",
            enabled_key="browser_tools.enabled",
            requires_approval=True,
            risk="medium",
            parameters={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript code to evaluate."}
                },
                "required": ["script"],
            },
            handler=browser_evaluate,
        )
    )
    registry.register(
        ToolDefinition(
            name="spawn_subagent",
            description="Spawn a subagent to work on a sub-task independently. The subagent runs in the background with role-restricted tools and reports back its findings. Use this for research, code review, testing, or any task that can run in parallel.",
            enabled_key="subagents.enabled",
            parameters={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["researcher", "coder", "tester", "reviewer", "safety", "planner", "browser_qa", "custom"],
                        "description": "Subagent role Ã¢â‚¬â€ determines available tools and permissions.",
                    },
                    "goal": {
                        "type": "string",
                        "description": "The task description for the subagent.",
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "If true (default), block and return the result. If false, return the subagent ID immediately.",
                    },
                },
                "required": ["role", "goal"],
            },
            handler=spawn_subagent,
        )
    )
    # MCP control tools — allow the model to inspect and manage MCP servers
    for ctrl_def in CONTROL_TOOL_DEFS:
        registry.register(ctrl_def)
    return registry
