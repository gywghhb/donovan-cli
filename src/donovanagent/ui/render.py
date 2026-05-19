from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from donovanagent import __version__
from donovanagent.config.manager import mask_secret
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.tools.base import ToolResult
from donovanagent.utils.json import dumps


GREETINGS = [
    "Good to see you.",
    "Ready when you are.",
    "What are we building?",
    "Let us get to work.",
    "Workspace loaded.",
    "Standing by.",
]

_LOGO_PATH = Path(__file__).parent / "TransparentLightLogo.png"

_MODE_DISPLAY = {
    "readonly": "readonly",
    "review": "review",
    "workspace": "workspace",
    "full_autonomy": "full autonomy",
}

VERSION = f"v{__version__}"

# ANSI Shadow figlet font for "DonovanAgent"
_ASCII_ART = [
    "██████╗  ██████╗ ███╗   ██╗ ██████╗ ██╗   ██╗ █████╗ ███╗   ██╗     █████╗  ██████╗ ███████╗███╗   ██╗████████╗",
    "██╔══██╗██╔═══██╗████╗  ██║██╔═══██╗██║   ██║██╔══██╗████╗  ██║    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝",
    "██║  ██║██║   ██║██╔██╗ ██║██║   ██║██║   ██║███████║██╔██╗ ██║    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ",
    "██║  ██║██║   ██║██║╚██╗██║██║   ██║╚██╗ ██╔╝██╔══██║██║╚██╗██║    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ",
    "██████╔╝╚██████╔╝██║ ╚████║╚██████╔╝ ╚████╔╝ ██║  ██║██║ ╚████║    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ",
    "╚═════╝  ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝   ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═══╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ",
]

_CAPABILITIES = [
    "read & write any file",
    "run shell commands (macOS/Linux/Windows)",
    "execute Python",
    "web search",
    "edit & patch files",
    "auto-fix errors (install + retry)",
    "user-defined skills",
    "activity stream",
    "plan mode",
    "persistent memory",
    "project context",
    "execution backends (local, Docker, SSH)",
    "browser automation",
    "checkpoints",
    "scheduled tasks",
    "subagents",
    "self-improving skills",
    "thinking summaries",
    "MCP server integration",
]


def print_startup(console: Console, config: DonovanAgentConfig) -> None:
    mode_display = _MODE_DISPLAY.get(config.app.permission_mode, config.app.permission_mode)
    model = config.provider.model or "not configured"
    workspace = config.app.default_workspace

    # Ã¢â€â‚¬Ã¢â€â‚¬ ASCII art block (white main, dim shadow offset) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    art = Text()
    # Version tag above the art with spacing
    art.append(f"{VERSION}\n\n", style="dim white")
    # Shadow pass Ã¢â‚¬â€ print dim offset lines first then overwrite with main
    # We render as layered Text: shadow in dim, main in bright white
    for i, line in enumerate(_ASCII_ART):
        art.append(line + "\n", style="bold white")

    # Ã¢â€â‚¬Ã¢â€â‚¬ Info line below art Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
    info = Text()
    info.append(model, style="white")
    info.append(f"    {workspace}", style="dim white")
    info.append("\n", style="dim white")
    info.append("Developed by Tudor Iustin", style="dim white")

    # Commands + capabilities columns
    commands = [
        "/help", "/model", "/tools", "/resume",
        "/skills", "/doctor", "/config", "/exit",
        "/new", "/workspace", "/mode", "/search",
        "/activity", "/think", "/plan", "/memory",
        "/context", "/backend", "/browser", "/checkpoint",
        "/schedule", "/subagent", "/skill", "/mcp",
    ]
    commands_text = Text()
    commands_text.append("Commands\n", style="bold white")
    for i in range(0, len(commands), 2):
        pair = f"{commands[i]:<16}{commands[i+1] if i+1 < len(commands) else ''}"
        commands_text.append(pair + "\n", style="dim white")

    caps_text = Text()
    caps_text.append("Capabilities\n", style="bold white")
    for cap in _CAPABILITIES:
        caps_text.append(f"  {cap}\n", style="dim white")

    left = Text()
    left.append_text(art)
    left.append("\n")
    left.append_text(info)

    console.print(Panel(
        Columns([left, commands_text, caps_text], equal=False, expand=True, padding=(0, 4)),
        box=box.ROUNDED,
        border_style="white",
        padding=(1, 2),
    ))


def error_panel(message: str, title: str = "Error") -> Panel:
    return Panel(str(message), title=title, border_style="red", box=box.ROUNDED)


def info_panel(message: str, title: str = "DonovanAgent") -> Panel:
    return Panel(str(message), title=title, border_style="white", box=box.ROUNDED)


def config_table(data: dict[str, Any]) -> Table:
    table = Table(title="DonovanAgent Config", box=box.SIMPLE_HEAVY)
    table.add_column("Key", style="bold")
    table.add_column("Value")

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(f"{prefix}.{key}" if prefix else key, child)
        elif isinstance(value, list):
            table.add_row(prefix, "\n".join(str(item) for item in value))
        else:
            rendered = "" if value is None else str(value)
            if is_secret_key(prefix):
                rendered = mask_secret(rendered)
            table.add_row(prefix, rendered)

    walk("", data)
    return table


def tools_table(rows: list[dict[str, Any]]) -> Table:
    table = Table(title="Tools", box=box.SIMPLE_HEAVY)
    table.add_column("Tool", style="bold")
    table.add_column("Enabled")
    table.add_column("Approval")
    table.add_column("Risk")
    table.add_column("Description")
    for row in rows:
        table.add_row(
            row["name"],
            "yes" if row["enabled"] else "no",
            "yes" if row["requires_approval"] else "no",
            row["risk"],
            row["description"],
        )
    return table


def sessions_table(rows: list[dict[str, Any]]) -> Table:
    table = Table(title="Sessions", box=box.SIMPLE_HEAVY)
    table.add_column("Updated")
    table.add_column("Title")
    table.add_column("Model")
    table.add_column("Workspace")
    table.add_column("ID")
    for row in rows:
        table.add_row(
            str(row.get("updated_at", "")),
            str(row.get("title", "")),
            f"{row.get('provider', '')}/{row.get('model', '')}",
            str(row.get("workspace", "")),
            str(row.get("id", "")),
        )
    return table


def tool_call_panel(name: str, args: dict[str, Any], risk: str = "low") -> Panel:
    body = dumps(args)
    return Panel(body, title=f"Tool: {name} | Risk: {risk}", border_style="cyan", box=box.ROUNDED)


def tool_result_panel(result: ToolResult) -> Panel:
    style = "green" if result.success else "red"
    content = result.content if result.content else dumps(result.data)
    if len(content) > 6000:
        content = content[:6000] + "\n... output truncated ..."
    return Panel(content, title="Tool result", border_style=style, box=box.ROUNDED)


def print_markdownish(console: Console, text: str) -> None:
    console.print(Text.from_markup(text, style="white"))


# Regex patterns for stripping tool-call markup from model output
# DSML/XML invoke blocks: <invoke name="tool">... <parameter name="x">...</parameter> ... </invoke>
_DSML_INVOKE_RE = re.compile(
    r"<invoke\s+name=['\"][^'\"]*['\"]>.*?</invoke>",
    re.DOTALL | re.IGNORECASE,
)
# Anthropic-style XML tool blocks: <tool_name>content</tool_name>
_XML_TOOL_RE = re.compile(
    r'<(write_file|edit_file|patch_file|read_file|run_command|run_shell|'
    r'list_directory|search_files|web_search|create_file|execute)'
    r'\b[^>]*>.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)
# Broad DSML blocks matching any mcp* tool call pattern
_BROAD_DSML_RE = re.compile(
    r"<invoke\s+name=['\"][^'\"]*['\"]>.*?</invoke>",
    re.DOTALL | re.IGNORECASE,
)
# JSON tool call objects: {"type":"tool_call", ...} and similar
_JSON_TOOL_CALL_RE = re.compile(
    r'\{\s*"type"\s*:\s*"tool_call"\s*.*?"arguments"\s*:\s*\{.*?\}\s*\}',
    re.DOTALL,
)
# OpenAI-style function call blocks in content
_OPENAI_TOOL_CALL_RE = re.compile(
    r'(?:<function_calls>|<tool_calls>).*?(?:</function_calls>|</tool_calls>)',
    re.DOTALL | re.IGNORECASE,
)
# Large fenced code blocks (15+ lines) — likely generated source code, not a quick command or snippet
_LARGE_CODE_BLOCK_RE = re.compile(
    r'```[a-zA-Z0-9_+-]*\n(?:[^\n]*\n){14,}?```',
    re.DOTALL,
)


def _strip_raw_markup(value: str) -> str:
    """Strip DSML, XML, and JSON tool-call markup from response text."""
    value = _DSML_INVOKE_RE.sub("", value)
    value = _XML_TOOL_RE.sub("", value)
    value = _BROAD_DSML_RE.sub("", value)
    value = _JSON_TOOL_CALL_RE.sub("", value)
    value = _OPENAI_TOOL_CALL_RE.sub("", value)
    # Strip any stray <invoke> or </invoke> tags
    value = re.sub(r'</?invoke[^>]*>', '', value, flags=re.IGNORECASE)
    value = re.sub(r'</?parameter[^>]*>', '', value, flags=re.IGNORECASE)
    value = re.sub(r"<parameter\s+name=['\"][^'\"]*['\"]\s*>", '', value, flags=re.IGNORECASE)
    # Strip <function>...</function> blocks (alternative format)
    value = re.sub(r"<function\s+name=['\"][^'\"]*['\"]>.*?</function>", '', value, flags=re.DOTALL | re.IGNORECASE)
    # Strip JSON blocks that look like tool call payloads (any depth)
    value = re.sub(
        r'\{\s*"(?:type|tool|name|function|arguments)"\s*:.*?"arguments"\s*:\s*\{.*?\}\s*\}',
        '', value, flags=re.DOTALL,
    )
    # Strip tool_calls wrapper tags (standalone, not part of a full block)
    value = re.sub(r'</?tool_calls>', '', value, flags=re.IGNORECASE)
    # Catch-all: strip any remaining DSML-like invoke/parameter blocks with either quote style
    value = re.sub(r"<invoke\s+name=['\"][^'\"]*['\"][^>]*>", '', value, flags=re.IGNORECASE)
    value = re.sub(r"</invoke>", '', value, flags=re.IGNORECASE)
    value = re.sub(r"<parameter\s+[^>]*>", '', value, flags=re.IGNORECASE)
    value = re.sub(r"</parameter>", '', value, flags=re.IGNORECASE)
    return value


def strip_tool_markup(value: str) -> str:
    """Strip only tool-call markup from text, preserving code blocks and content.

    Use this before storing model responses in conversation history so the
    model never sees raw tool-call syntax and learns to reproduce it.
    """
    return _strip_raw_markup(value)


def sanitize_response(value: str) -> str:
    """Sanitize model output for user-facing display.

    Strips raw tool-call markup, replaces large generated code blocks with
    a summary placeholder, and normalises whitespace.
    """
    value = value.replace("\r\n", "\n")
    # Strip raw tool-call markup first
    value = _strip_raw_markup(value)
    # Replace large generated code blocks (>15 lines) with a placeholder
    value = _LARGE_CODE_BLOCK_RE.sub("\n[code generated - omitted from response]\n", value)
    return value.strip()


def plain_text(text: str) -> str:
    """Best-effort Markdown-to-readable-plain-text conversion for model output.

    Applies sanitization first (strips raw tool-call markup), then normalises
    markdown syntax to plain text.
    """
    value = text.replace("\r\n", "\n")
    # Sanitize: strip raw tool-call markup first
    value = _strip_raw_markup(value)
    value = _LARGE_CODE_BLOCK_RE.sub("\n[code generated - omitted from response]\n", value)
    # Then normalise markdown to plain text
    value = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", value)
    value = value.replace("```", "")
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value, flags=re.MULTILINE)
    value = re.sub(r"(\*\*|__)(.*?)\1", r"\2", value)
    value = re.sub(r"(\*|_)(.*?)\1", r"\2", value)
    value = re.sub(r"^\s{0,3}>\s?", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s{0,3}[-*+]\s+", "- ", value, flags=re.MULTILINE)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _parse_markdown_table(block: str) -> Table | None:
    """Parse a markdown table block into a Rich Table."""
    lines = block.strip().split("\n")
    if len(lines) < 3:
        return None
    if not lines[0].strip().startswith("|"):
        return None
    if not re.match(r"^\|[\s\-:|]+\|$", lines[1].strip()):
        return None

    headers = [h.strip() for h in lines[0].strip().split("|")[1:-1]]
    if not headers:
        return None

    table = Table(*headers, box=box.SIMPLE_HEAVY)
    for line in lines[2:]:
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.split("|")[1:-1]]
        if cells:
            table.add_row(*cells)
    return table


def assistant_panel(text: str) -> Panel:
    blocks = re.split(r"\n{2,}", text)
    renderables: list[Panel | Table | Text] = []

    for block in blocks:
        table = _parse_markdown_table(block)
        if table is not None:
            renderables.append(table)
        else:
            cleaned = plain_text(block)
            if cleaned:
                renderables.append(Text(cleaned + "\n", style="white"))

    if not renderables:
        renderables.append(Text("", style="white"))

    return Panel(
        Group(*renderables),
        border_style="white",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def tools_used_panel(tool_names: list[str]) -> Panel | None:
    if not tool_names:
        return None
    unique = []
    for name in tool_names:
        if name not in unique:
            unique.append(name)
    body = "\n".join(f"  {name}" for name in unique)
    return Panel(
        Text(body, style="green"),
        title="[bold green]Tools used[/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def context_footer(used_tokens: int, context_window: int) -> Text:
    context_window = max(context_window, 1)
    percent = min(100, round((used_tokens / context_window) * 100, 1))
    return Text(f"Context: {used_tokens:,} / {context_window:,} tokens ({percent}% used)", style="dim white")


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    leaf = lowered.rsplit(".", 1)[-1]
    if leaf.endswith("_env"):
        return False
    return (
        leaf in {"api_key", "token", "secret"}
        or leaf.endswith("_api_key")
        or leaf.endswith("_token")
        or leaf.endswith("_secret")
        or leaf in {"api_key_value", "token_value", "secret_value"}
        or "password" in leaf
        or lowered.endswith(".token")
        or "authorization" in lowered
    )
