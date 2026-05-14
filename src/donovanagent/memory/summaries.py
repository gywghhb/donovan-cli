from __future__ import annotations

from typing import Any

from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


def generate_session_summary(db: MemoryDatabase, session_id: str, messages: list[dict[str, Any]]) -> str:
    """Generate a compact session summary string."""
    tools_used: set[str] = set()
    files_changed: list[str] = []
    user_requests: list[str] = []
    errors: list[str] = []
    solutions: list[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        meta = _safe_parse_meta(msg.get("metadata_json", "{}"))

        if role == "user" and content:
            user_requests.append(content[:200])
        elif role == "assistant" and content and content != "[tool call]":
            if any(word in content.lower() for word in ("error", "failed", "fix", "issue")):
                solutions.append(content[:200])
        elif role == "tool":
            tool_name = meta.get("tool_name", "")
            if tool_name:
                tools_used.add(tool_name)
            if tool_name in ("write_file", "patch_file"):
                args = _safe_parse_meta(meta.get("arguments", {}))
                if isinstance(args, dict) and args.get("path"):
                    files_changed.append(str(args["path"]))
            if not meta.get("success", True) and meta.get("exit_code") not in (None, 0):
                err_msg = content[:100]
                if err_msg:
                    errors.append(err_msg)

    # Build compact text
    parts: list[str] = []
    if user_requests:
        parts.append(f"Request: {user_requests[0][:100]}")
    if tools_used:
        parts.append(f"Tools: {', '.join(sorted(tools_used))}")
    if files_changed:
        parts.append(f"Changed: {', '.join(files_changed[:5])}")
    if errors:
        parts.append(f"Errors: {errors[0][:100]}")
    if solutions:
        parts.append(f"Result: {solutions[-1][:200]}")

    return " | ".join(parts) if parts else f"Session {session_id[:8]}"


def _safe_parse_meta(data: Any) -> dict[str, Any]:
    import json
    if isinstance(data, dict):
        return data
    if isinstance(data, str) and data:
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}
