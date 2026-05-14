from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
    reasoning_content: str = ""  # DeepSeek reasoning_content — must be echoed back
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ModelInfo:
    id: str
    provider: str
    details: dict[str, Any] = field(default_factory=dict)
