from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from donovanagent.ui.prompt import SlashCommandCompleter


def completions_for(text: str) -> list[str]:
    completer = SlashCommandCompleter()
    event = CompleteEvent(completion_requested=True)
    return [completion.text for completion in completer.get_completions(Document(text), event)]


def test_slash_completion_works_anywhere() -> None:
    assert "/clear" in completions_for("/cl")
    assert "/clear" in completions_for("hey /cl")
    assert "/clear" in completions_for("hey\n/cl")
    assert "/model set" in completions_for("please /model s")


def test_normal_messages_do_not_autocomplete() -> None:
    assert completions_for("hey") == []
