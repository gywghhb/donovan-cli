from __future__ import annotations

from typing import Any

from donovanagent.providers.models import ToolCall
from donovanagent.utils.json import extract_marked_json_object


def parse_fallback_tool_call(text: str) -> ToolCall | None:
    obj = extract_marked_json_object(text)
    if not obj or obj.get("type") != "tool_call":
        return None
    tool = obj.get("tool")
    args = obj.get("arguments")
    if not isinstance(tool, str) or not isinstance(args, dict):
        return None
    return ToolCall(id=f"fallback_{tool}", name=tool, arguments=args)


def assistant_message_with_tool_calls(
    content: str,
    calls: list[ToolCall],
    reasoning_content: str = "",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if reasoning_content:
        # DeepSeek reasoning models require this field echoed back verbatim
        message["reasoning_content"] = reasoning_content
    if calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": __import__("json").dumps(call.arguments),
                },
            }
            for call in calls
        ]
    return message
