"""MCP UI rendering components.

Provides Rich-based tables and panels for MCP status display.
"""

from __future__ import annotations

import time
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from donovanagent.mcp.config import mask_secret, mask_url
from donovanagent.mcp.manager import McpServerStatus, McpManager
from donovanagent.mcp.client import McpToolInfo, McpResourceInfo, McpPromptInfo


def _time_ago(timestamp: float) -> str:
    """Format a Unix timestamp as a relative time string."""
    if timestamp <= 0:
        return "never"
    elapsed = time.time() - timestamp
    if elapsed < 60:
        return "just now"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m ago"
    if elapsed < 86400:
        return f"{int(elapsed // 3600)}h ago"
    return f"{int(elapsed // 86400)}d ago"


def _trust_display(trust: str) -> str:
    mapping = {
        "trusted": "trusted",
        "ask": "needs trust",
        "blocked": "blocked",
    }
    return mapping.get(trust, trust)


def mcp_status_panel(statuses: list[McpServerStatus]) -> Panel:
    """Render the main /mcp status panel."""
    if not statuses:
        return Panel(
            Text("No MCP servers configured.\nUse 'donovan mcp add' to add a server.", style="dim"),
            title="MCP Servers",
            border_style="white",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Name", style="white", no_wrap=True)
    table.add_column("Type", style="dim")
    table.add_column("Status", no_wrap=True)
    table.add_column("Trust", style="dim")
    table.add_column("Tools", justify="right")
    table.add_column("Resources", justify="right")
    table.add_column("Prompts", justify="right")
    table.add_column("Last Seen", style="dim")

    for s in statuses:
        status_text = "connected" if s.connected else "disconnected"
        status_style = "green" if s.connected else "dim"

        trust_text = _trust_display(s.trust)
        trust_style = "yellow" if s.trust == "ask" else \
                     "red" if s.trust == "blocked" else "green"

        last_seen = _time_ago(s.last_connected) if s.connected else "-"
        if s.last_error:
            last_seen = f"[red]error[/red]"

        table.add_row(
            s.name,
            s.type,
            f"[{status_style}]{status_text}[/{status_style}]",
            f"[{trust_style}]{trust_text}[/{trust_style}]",
            str(s.tool_count) if s.connected else "-",
            str(s.resource_count) if s.connected else "-",
            str(s.prompt_count) if s.connected else "-",
            last_seen,
        )

    conn_count = sum(1 for s in statuses if s.connected)
    total = len(statuses)
    subtitle = Text(
        f"{conn_count}/{total} connected  |  "
        "Actions: /mcp connect <name>  /mcp trust <name>  /mcp logs <name>",
        style="dim",
    )

    return Panel(
        Group(table, Text(), subtitle),
        title="MCP Servers",
        border_style="white",
        box=box.ROUNDED,
    )


def mcp_tool_panel(tools: list[McpToolInfo]) -> Panel:
    """Render a table of MCP tools."""
    if not tools:
        return Panel(
            Text("No tools discovered.", style="dim"),
            title="MCP Tools",
            border_style="white",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Tool Name", style="white", no_wrap=True)
    table.add_column("Description", style="dim")
    table.add_column("Parameters", justify="right", style="dim")

    for t in tools:
        param_count = len(t.inputSchema.get("properties", {})) if t.inputSchema else 0
        desc = (t.description or "(no description)")[:80]
        table.add_row(t.name, desc, str(param_count))

    return Panel(
        table,
        title="MCP Tools",
        border_style="white",
        box=box.ROUNDED,
    )


def mcp_resource_panel(resources: list[McpResourceInfo]) -> Panel:
    """Render a table of MCP resources."""
    if not resources:
        return Panel(
            Text("No resources discovered.", style="dim"),
            title="MCP Resources",
            border_style="white",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("URI", style="white", no_wrap=True)
    table.add_column("Name", style="white")
    table.add_column("Description", style="dim")
    table.add_column("Type", style="dim")

    for r in resources[:30]:
        desc = (r.description or "")[:60]
        table.add_row(r.uri, r.name, desc, r.mimeType or "-")

    if len(resources) > 30:
        table.add_row(f"... and {len(resources) - 30} more", "", "", "")

    return Panel(
        table,
        title="MCP Resources",
        border_style="white",
        box=box.ROUNDED,
    )


def mcp_prompt_panel(prompts: list[McpPromptInfo]) -> Panel:
    """Render a table of MCP prompts."""
    if not prompts:
        return Panel(
            Text("No prompts discovered.", style="dim"),
            title="MCP Prompts",
            border_style="white",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Name", style="white", no_wrap=True)
    table.add_column("Description", style="dim")
    table.add_column("Arguments", justify="right")

    for p in prompts:
        arg_count = len(p.arguments)
        desc = (p.description or "")[:80]
        table.add_row(p.name, desc, str(arg_count))

    return Panel(
        table,
        title="MCP Prompts",
        border_style="white",
        box=box.ROUNDED,
    )


def mcp_tool_call_panel(server_name: str, tool_name: str, risk: str,
                        status: str = "running") -> Panel:
    """Render a tool call panel for the activity stream."""
    color = "green" if status == "completed" else \
            "red" if status == "failed" else "yellow"
    return Panel(
        Text.assemble(
            (f"{server_name}.{tool_name}\n", "white bold"),
            (f"Server: {server_name}  Risk: {risk}\n", "dim"),
            (f"Status: {status}", color),
        ),
        title="MCP Tool",
        border_style=color,
        box=box.SIMPLE,
    )


def mcp_log_panel(logs: list[str] | None) -> Panel:
    """Render MCP server logs."""
    if not logs:
        return Panel(
            Text("No log entries.", style="dim"),
            title="MCP Logs",
            border_style="white",
            box=box.ROUNDED,
        )

    # Mask secrets in logs
    sanitized: list[str] = []
    for line in logs:
        sanitized_line = mask_url(line)
        for secret_word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL"):
            if secret_word in line.upper():
                sanitized_line = "[MASKED]"
                break
        sanitized.append(sanitized_line)

    text = "\n".join(sanitized[-50:])  # Show last 50 lines
    return Panel(
        Text(text, style="dim"),
        title="MCP Logs",
        border_style="white",
        box=box.ROUNDED,
    )


def mcp_trust_prompt(server_name: str, server_type: str,
                     command: str = "", url: str = "",
                     args: list[str] | None = None,
                     env_keys: list[str] | None = None,
                     headers_keys: list[str] | None = None,
                     scope: str = "") -> Panel:
    """Render the trust prompt panel."""

    from rich.text import Text as RichText
    lines: list[RichText] = []

    if server_type == "stdio":
        cmd_line = f"Command: {command} {' '.join(args or [])}"
        lines.append(RichText(cmd_line, style="white"))
        if env_keys:
            env_str = ", ".join(f"{k}=[MASKED]" if ("KEY" in k.upper() or "TOKEN" in k.upper() or "SECRET" in k.upper()) else k for k in env_keys)
            lines.append(RichText(f"Env: {env_str}", style="dim"))
    elif server_type in ("http", "sse"):
        lines.append(RichText(f"URL: {mask_url(url)}", style="white"))
        if headers_keys:
            headers_str = ", ".join(f"{k}=[MASKED]" for k in headers_keys)
            lines.append(RichText(f"Headers: {headers_str}", style="dim"))

    lines.append(RichText(""))
    lines.append(RichText(
        "MCP servers can read data, call APIs, and modify systems.\n"
        "Only trust servers you understand and trust.",
        style="yellow",
    ))
    if scope:
        lines.append(RichText(f"Scope: {scope}", style="dim"))

    return Panel(
        Group(*lines),
        title=f"Trust MCP Server: {server_name}",
        border_style="white",
        box=box.ROUNDED,
    )
