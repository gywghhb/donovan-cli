from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style


SLASH_COMMANDS = [
    "/help",
    "/model",
    "/model set",
    "/tools",
    "/search",
    "/workspace",
    "/workspace add",
    "/workspace remove",
    "/mode readonly",
    "/mode review",
    "/mode workspace",
    "/mode full_autonomy",
    "/new",
    "/sessions",
    "/skills",
    "/skill_add",
    "/skill_list",
    "/skill_remove",
    "/history",
    "/clear",
    "/doctor",
    "/config",
    "/resume",
    "/exit",
    # New feature commands
    "/activity",
    "/activity on",
    "/activity off",
    "/activity compact",
    "/activity verbose",
    "/think",
    "/think on",
    "/think off",
    "/think status",
    "/plan",
    "/plan on",
    "/plan off",
    "/plan show",
    "/plan approve",
    "/plan cancel",
    "/memory",
    "/memory search",
    "/memory add",
    "/memory forget",
    "/memory summarize",
    "/recall",
    "/context",
    "/context project",
    "/context refresh",
    "/backend",
    "/backend local",
    "/backend docker",
    "/backend ssh",
    "/browser open",
    "/browser close",
    "/browser screenshot",
    "/browser text",
    "/browser url",
    "/browser back",
    "/browser reload",
    "/checkpoint list",
    "/checkpoint show",
    "/checkpoint diff",
    "/checkpoint restore",
    "/checkpoint delete",
    "/schedule list",
    "/schedule remove",
    "/schedule pause",
    "/schedule resume",
    "/schedule run",
    "/subagent",
    "/subagents",
    "/subagents create",
    "/subagents list",
    "/subagents kill",
    "/subagents result",
    "/subagents on",
    "/subagents off",
    "/skill",
    "/skill list",
    "/skill search",
    "/skill show",
    "/skill delete",
    "/skill drafts",
    "/skill approve",
    "/skill reject",
    "/skill add",
    "/skill_drafts",
    "/skill_approve",
    "/skill_reject",
    "/skill_show",
    "/skill_use",
    "/skill_disable",
    "/skill_enable",
    "/skill_delete",
    "/skill_learn",
    # MCP commands
    "/mcp",
    "/mcp list",
    "/mcp connect",
    "/mcp disconnect",
    "/mcp trust",
    "/mcp block",
    "/mcp tools",
    "/mcp resources",
    "/mcp prompts",
    "/mcp logs",
    "/mcp auth",
    "/mcp refresh",
]


class DonovanAgentCompleter(Completer):
    """Completer that handles both slash commands and @ file references."""

    def __init__(self) -> None:
        self.slash_completer = SlashCommandCompleter()

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> list[Completion]:
        text = document.text_before_cursor
        slash_index = text.rfind("/")
        at_index = text.rfind("@")
        if slash_index >= 0 and slash_index > at_index:
            return list(self.slash_completer.get_completions(document, complete_event))

        # @ file references â€” check anywhere in the text
        if "@" in text:
            at_index = text.rfind("@")
            rest = text[at_index + 1:]
            partial_match = re.match(r"[\w.\-/]*", rest)
            partial = partial_match.group() if partial_match else ""
            # Don't complete if @ is inside a word (not a file reference)
            if at_index == 0 or not re.match(r"[\w]", text[at_index - 1]):
                results: list[Completion] = []
                try:
                    cwd = Path(os.getcwd())
                    for child in sorted(cwd.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                        name = child.name
                        if partial and partial.lower() not in name.lower():
                            continue
                        results.append(Completion(
                            name,
                            start_position=-len(partial) if partial else 0,
                            display=f"{name}/" if child.is_dir() else name,
                            display_meta="dir" if child.is_dir() else "file",
                        ))
                        if len(results) >= 40:
                            break
                except OSError:
                    pass
                return results

        return list(self.slash_completer.get_completions(document, complete_event))


class SlashCommandCompleter(Completer):
    """Completer for slash commands typed anywhere in a prompt."""

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> list[Completion]:
        text = document.text_before_cursor
        slash_index = text.rfind("/")
        if slash_index < 0:
            return []

        fragment = text[slash_index:]
        lowered = fragment.lower()
        return [
            Completion(cmd, start_position=-len(fragment))
            for cmd in SLASH_COMMANDS
            if cmd.lower().startswith(lowered)
        ]


def DonovanAgent_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event) -> None:
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("tab")
    def _(event) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=False)

    @bindings.add("s-tab")
    def _(event) -> None:
        buffer = event.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()
        else:
            buffer.start_completion(select_first=False)

    return bindings


def create_prompt_session(history_file: Path) -> PromptSession[str]:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(history_file)),
        completer=DonovanAgentCompleter(),
        complete_while_typing=True,
        complete_in_thread=True,
        key_bindings=DonovanAgent_key_bindings(),
        style=Style.from_dict(
            {
                "": "#ffffff",
                "prompt": "#888888 bold",
                "completion-menu": "bg:#000000",
                "completion-menu.completion": "bg:#000000 #888888",
                "completion-menu.completion.current": "bg:#000000 #ffffff bold",
                "completion-menu.meta.completion": "bg:#000000 #555555",
                "completion-menu.meta.completion.current": "bg:#000000 #888888",
                "bottom-toolbar": "noinherit bg:default fg:default noreverse",
                "bottom-toolbar.text": "noinherit bg:default fg:default noreverse",
            }
        ),
        multiline=True,
        reserve_space_for_menu=0,
    )
