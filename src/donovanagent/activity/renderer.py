from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from donovanagent.activity.events import AgentActivityEvent


_HOME = str(Path.home()).replace("\\", "/")


def _strip_home_paths(text: str) -> str:
    """Replace common home directory patterns with ~ for privacy."""
    # Windows: C:\Users\tudor\...  and  \Users\tudor\...
    text = re.sub(r"""(?i)[A-Za-z]:\\(?:Users|home|Documents)[^\s"'`)\]]+""",
                  lambda m: _shorten_path(m.group(0).replace("\\", "/")), text)
    text = re.sub(r"""(?i)\\(?:Users|home)[^\s"'`)\]]+""",
                  lambda m: _shorten_path(m.group(0).replace("\\", "/")), text)
    # Git Bash: /c/Users/tudor/... or /c/Users/...
    text = re.sub(r"""/(?:[A-Za-z])/(?:Users|home)[^\s"'`)\]]+""",
                  lambda m: _shorten_path(m.group(0)), text)
    # Unix home: /home/user/...
    text = re.sub(r"""/home/[^/\s][^\s"'`)\]]*""",
                  lambda m: _shorten_path(m.group(0)), text)
    return text


def _shorten_path(path: str) -> str:
    if _HOME and _HOME in path:
        path = path.replace(_HOME, "~")
    if len(path) <= 50:
        return path
    parts = path.split("/")
    if len(parts) > 3:
        return parts[0] + "/.../" + parts[-1]
    return path[:50] + "..."


def _preview_args(tool_name: str, args: dict[str, Any] | None, mode: str = "preview") -> str:
    if not args:
        return ""
    if mode == "none":
        return ""
    if mode == "full":
        return _strip_home_paths(json.dumps(args, ensure_ascii=False)[:300])
    # preview mode
    if tool_name == "run_shell":
        cmd = str(args.get("command", ""))
        return _strip_home_paths(cmd[:120] + ("..." if len(cmd) > 120 else ""))
    elif tool_name == "web_search":
        return f"Query: {str(args.get('query', ''))[:80]}"
    elif tool_name == "search_files":
        return f"Search: {str(args.get('query', ''))[:80]}"
    elif tool_name == "list_directory":
        return f"Path: {_strip_home_paths(str(args.get('path', ''))[:80])}"
    elif tool_name in ("read_file", "write_file", "patch_file"):
        return f"Path: {_strip_home_paths(str(args.get('path', ''))[:80])}"
    elif tool_name == "execute":
        code = str(args.get("code", ""))
        first_line = code.strip().split("\n")[0] if code.strip() else ""
        return first_line[:80] + ("..." if len(first_line) > 80 else "") if first_line else "Running Python"
    elif tool_name == "browser_open":
        return f"URL: {str(args.get('url', ''))[:80]}"
    return _strip_home_paths(json.dumps({k: str(v)[:60] for k, v in list(args.items())[:3]}, ensure_ascii=False))


_CHECKLIST_SYMBOLS = {
    "pending": "â—‹",   # â—‹
    "active": "â—",    # â—
    "completed": "âœ“", # âœ“
    "failed": "âœ•",    # âœ•
    "skipped": "â€“",   # â€“
    "changed": "â†»",   # â†»
    "blocked": "!",
}


def _format_duration(ms: int | None) -> str:
    if ms is None:
        return ""
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


def _elapsed_display(start_ms: int | None, end_ms: int | None = None) -> str:
    if start_ms is None:
        return ""
    end = end_ms if end_ms is not None else 0
    elapsed = end - start_ms
    if end == 0:
        # Currently running
        return f"Elapsed: {_format_duration(elapsed)}"
    return f"Duration: {_format_duration(elapsed)}"


class ActivityRenderer:
    def __init__(self, console: Console, config: Any) -> None:
        self.console = console
        self.config = config

    def render_event(self, event: AgentActivityEvent, compact: bool | None = None) -> RenderableType | None:
        """Render a single activity event to a Rich renderable."""
        cfg = self.config.activity_stream if hasattr(self.config, "activity_stream") else None
        is_compact = compact if compact is not None else (cfg.compact if cfg else False)
        show_args = cfg.show_tool_args if cfg else "preview"
        show_timers = cfg.show_timers if cfg else True
        show_results = cfg.show_result_summaries if cfg else True

        if event.event_type in (
            "tool_started", "tool_selected", "tool_completed", "tool_failed", "tool_progress"
        ):
            return self._render_tool_card(event, is_compact, show_args, show_timers, show_results)
        elif event.event_type.startswith("checklist_"):
            return self._render_checklist_item(event, is_compact)
        elif event.event_type.startswith("subagent_"):
            return self._render_subagent(event, is_compact)
        elif event.event_type.startswith("planning_"):
            return self._render_planning(event, is_compact)
        elif event.event_type.startswith("memory_"):
            return self._render_memory(event, is_compact)
        elif event.event_type.startswith("checkpoint_"):
            return self._render_checkpoint(event, is_compact)
        elif event.event_type.startswith("browser_"):
            return self._render_browser(event, is_compact)
        elif event.event_type in ("skill_loaded", "skill_learned", "skill_draft_created"):
            return self._render_skill(event, is_compact)
        elif event.event_type.startswith("scheduler_"):
            return self._render_scheduler(event, is_compact)
        elif event.event_type in ("task_completed", "task_failed"):
            return self._render_task_result(event, is_compact)
        elif event.event_type == "backend_selected":
            return self._render_backend(event, is_compact)
        elif event.event_type == "agent_status":
            return self._render_status(event, is_compact)
        return None

    def _render_tool_card(
        self, event: AgentActivityEvent, compact: bool, show_args: str, show_timers: bool, show_results: bool
    ) -> Panel:
        lines: list[RenderableType] = []

        # Status badge
        if event.status == "running":
            spinner = Progress(
                SpinnerColumn("dots"),
                TextColumn("[bold yellow]{task.description}[/]"),
                TimeElapsedColumn(),
                transient=True,
            )
            spinner.add_task("Running...")
            lines.append(spinner)
        elif event.status == "completed":
            lines.append(Text("Status: completed", style="bold green"))
        elif event.status == "failed":
            lines.append(Text(f"Status: failed  {event.error or ''}", style="bold red"))
        elif event.status == "selected":
            lines.append(Text("Status: selected", style="bold cyan"))

        # Tool args
        if show_args != "none" and event.tool_args_preview:
            lines.append(Text(event.tool_args_preview, style="dim white"))

        # Timer
        if show_timers and event.elapsed_ms is not None:
            display = _elapsed_display(0, event.elapsed_ms) if event.status in ("completed", "failed") else f"Elapsed: {_format_duration(event.elapsed_ms)}"
            lines.append(Text(display, style="dim cyan"))

        # Result
        if show_results and event.result_summary:
            lines.append(Text(event.result_summary, style="green"))

        # Error
        if event.error:
            lines.append(Text(f"Error: {event.error}", style="bold red"))

        # Backend & subagent info
        meta_parts = []
        if event.backend and event.backend != "local":
            meta_parts.append(f"Backend: {event.backend}")
        if event.subagent_id:
            meta_parts.append(f"Subagent: {event.subagent_id}")
        if meta_parts:
            lines.append(Text("  ".join(meta_parts), style="dim white"))

        title = f"Tool: {event.tool_name}" if event.tool_name else "Tool"
        border = "yellow" if event.status == "running" else "green" if event.status == "completed" else "red" if event.status == "failed" else "cyan"

        content = Group(*lines) if lines else Text(event.message or "")
        return Panel(content, title=title, border_style=border, box=box.ROUNDED, padding=(0, 1))

    def _render_checklist_item(self, event: AgentActivityEvent, compact: bool) -> Text:
        symbol = _CHECKLIST_SYMBOLS.get(event.status or "pending", "â—‹")
        style = "bold green" if event.status == "completed" else "bold yellow" if event.status == "active" else "red" if event.status in ("failed",) else "dim white"
        return Text(f"  {symbol} {event.message}", style=style)

    def _render_subagent(self, event: AgentActivityEvent, compact: bool) -> Panel:
        label = "Subagent"
        if event.subagent_id:
            label += f": {event.subagent_id}"
        status_style = "green" if event.status == "completed" else "yellow" if event.status == "running" else "red" if event.status == "failed" else "white"
        content = Text(event.message or "", style=status_style)
        if event.result_summary:
            content.append(f"\n{event.result_summary}", style="dim white")
        return Panel(content, title=label, border_style=status_style, box=box.ROUNDED, padding=(0, 1))

    def _render_planning(self, event: AgentActivityEvent, compact: bool) -> Text:
        return Text(f"  {event.message}", style="bold cyan")

    def _render_memory(self, event: AgentActivityEvent, compact: bool) -> Text:
        style = "dim magenta"
        return Text(f"  {event.message}", style=style)

    def _render_checkpoint(self, event: AgentActivityEvent, compact: bool) -> Text:
        return Text(f"  {event.message}", style="dim yellow")

    def _render_browser(self, event: AgentActivityEvent, compact: bool) -> Panel:
        lines = [Text(event.message or "", style="white")]
        if event.result_summary:
            lines.append(Text(event.result_summary, style="dim white"))
        if event.tool_args_preview:
            lines.append(Text(event.tool_args_preview, style="dim white"))
        border = "yellow" if event.status == "running" else "green"
        return Panel(Group(*lines), title=f"Browser: {event.tool_name or ''}", border_style=border, box=box.ROUNDED, padding=(0, 1))

    def _render_skill(self, event: AgentActivityEvent, compact: bool) -> Text:
        return Text(f"  {event.message}", style="bold blue")

    def _render_scheduler(self, event: AgentActivityEvent, compact: bool) -> Panel:
        return Panel(Text(event.message or "", style="white"), title="Scheduler", border_style="blue", box=box.ROUNDED, padding=(0, 1))

    def _render_task_result(self, event: AgentActivityEvent, compact: bool) -> Panel:
        style = "bold green" if event.event_type == "task_completed" else "bold red"
        return Panel(Text(event.message or "", style=style), title="Task", border_style="green" if event.event_type == "task_completed" else "red", box=box.ROUNDED)

    def _render_backend(self, event: AgentActivityEvent, compact: bool) -> Text:
        return Text(f"  Backend: {event.message}", style="dim cyan")

    def _render_status(self, event: AgentActivityEvent, compact: bool) -> Text:
        return Text(f"  {event.message}", style="white")

    def render_checklist(self, items: list[dict[str, Any]]) -> Panel:
        """Render a full checklist as a Rich panel."""
        table = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 1))
        table.add_column("Status", style="bold")
        table.add_column("Item")
        for item in items:
            symbol = _CHECKLIST_SYMBOLS.get(item.get("status", "pending"), "â—‹")
            style = "green" if item["status"] == "completed" else "yellow" if item["status"] == "active" else "red" if item["status"] == "failed" else "dim white"
            table.add_row(f"[{style}]{symbol}[/]", f"[{style}]{item.get('title', '')}[/]")
        return Panel(table, title="Plan Checklist", border_style="white", box=box.ROUNDED)
