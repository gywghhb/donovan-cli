from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.text import Text

_APPROVAL_TIMEOUT_SECONDS = 45


_PROMPT_STYLE = Style.from_dict({
    "": "bg:#1a1a1a #ffffff",
    "prompt": "bg:#1a1a1a #ffffff bold",
})

_TOOL_LABELS: dict[str, str] = {
    "run_shell":       "run shell command",
    "write_file":      "write file",
    "patch_file":      "edit file",
    "read_file":       "read file",
    "list_directory":  "list directory",
    "search_files":    "search files",
    "execute":         "run Python code",
    "kill_process":    "terminate process",
    "web_search":      "search the web",
    "get_system_info": "get system info",
}


@dataclass(frozen=True)
class ApprovalRequest:
    title: str
    body: str
    risk: str = "medium"
    typed_confirmation: bool = False
    typed_phrase: str = "I understand"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str


def _short_summary(title: str, body: str) -> str:
    """Return a one-line human-readable summary of what's being approved."""
    label = _TOOL_LABELS.get(title, title)
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    # Pull the most informative line
    detail = ""
    for line in lines:
        if line.startswith("Command:"):
            detail = line[8:].strip()
            break
        if line.startswith("Terminate process"):
            detail = line
            break
    if not detail and lines:
        # Use first non-metadata line
        skip_prefixes = ("Permission mode:", "Risk:", "Risk reasons:", "-")
        for line in lines:
            if not any(line.startswith(p) for p in skip_prefixes):
                detail = line[:80]
                break
    if detail:
        return f"{label}  —  {detail}"
    return label


def _activity_label(tool_name: str, args: dict) -> str:
    """Return a short present-tense description of what the tool is doing."""
    name = tool_name
    if name == "run_shell":
        cmd = str(args.get("command", ""))[:60]
        return f"running: {cmd}"
    if name == "write_file":
        p = Path(str(args.get("path", ""))).name
        return f"creating/writing: {p}"
    if name == "patch_file":
        p = Path(str(args.get("path", ""))).name
        return f"editing: {p}"
    if name == "read_file":
        p = Path(str(args.get("path", ""))).name
        return f"reading: {p}"
    if name == "list_directory":
        p = Path(str(args.get("path", "") or ".")).name or "."
        return f"listing: {p}"
    if name == "search_files":
        return f"searching: {args.get('query', '')}"
    if name == "execute":
        return "executing Python"
    if name == "kill_process":
        return f"terminating process {args.get('pid', '')}"
    if name == "web_search":
        return f"searching web: {args.get('query', '')}"
    return _TOOL_LABELS.get(name, name)


class ApprovalManager:
    def __init__(self, console: Console, assume_yes: bool = False, interactive: bool | None = None) -> None:
        self.console = console
        self.assume_yes = assume_yes
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    def request(self, request: ApprovalRequest) -> ApprovalDecision:
        if self.assume_yes and request.risk != "high":
            return ApprovalDecision(True, "approved automatically")

        if not self.interactive:
            return ApprovalDecision(False, "no interactive terminal")

        summary = _short_summary(request.title, request.body)
        risk_color = "red" if request.risk == "high" else "yellow"

        self.console.print()
        self.console.print(Text.assemble(
            ("Approval required  ", f"bold {risk_color}"),
            (summary, "white"),
        ))

        if request.typed_confirmation:
            answer = self._prompt_with_timeout(f"Type '{request.typed_phrase}' to confirm: ", timeout=_APPROVAL_TIMEOUT_SECONDS)
            if answer is None:
                self.console.print(f"  [dim]Timed out after {_APPROVAL_TIMEOUT_SECONDS}s, denying.[/dim]\n")
                return ApprovalDecision(False, f"timed out after {_APPROVAL_TIMEOUT_SECONDS}s")
            if answer.strip() != request.typed_phrase:
                self.console.print("  [dim]Cancelled.[/dim]\n")
                return ApprovalDecision(False, "typed confirmation did not match")

        answer = self._prompt_with_timeout("[y/n] ", timeout=_APPROVAL_TIMEOUT_SECONDS)
        if answer is None:
            self.console.print(f"  [dim]Timed out after {_APPROVAL_TIMEOUT_SECONDS}s, denying.[/dim]\n")
            return ApprovalDecision(False, f"timed out after {_APPROVAL_TIMEOUT_SECONDS}s")

        self.console.print()
        if answer.strip().lower() in {"y", "yes"}:
            return ApprovalDecision(True, "approved by user")
        return ApprovalDecision(False, "denied by user")

    def _prompt_with_timeout(self, prompt_text: str, timeout: int = 45) -> str | None:
        """Prompt the user with a timeout. Returns None if timed out."""
        result: list[str | None] = [None]

        def _ask() -> None:
            try:
                answer = pt_prompt(
                    HTML(f"<prompt>  {prompt_text}</prompt>"),
                    style=_PROMPT_STYLE,
                )
                result[0] = answer
            except (EOFError, KeyboardInterrupt):
                result[0] = ""

        thread = threading.Thread(target=_ask, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        return result[0]
