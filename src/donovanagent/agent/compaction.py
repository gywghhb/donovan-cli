from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from donovanagent.activity import ActivityService
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

# Estimate: 1 token Ã¢â€°Ë† 4 chars for English text
_CHARS_PER_TOKEN = 4

# Headroom for the response
_RESPONSE_HEADROOM = 4096


@dataclass
class CompactResult:
    session_id: str
    compacted_count: int
    summary: str
    summary_token_count: int
    tool_events: list[str]
    start_msg_id: int
    end_msg_id: int


class CompactionService:
    """Condenses older conversation messages when approaching the context limit."""

    def __init__(self, db: MemoryDatabase, config: DonovanAgentConfig) -> None:
        self.db = db
        self.config = config

    @property
    def _cfg(self) -> Any:
        return self.config.memory

    def should_compact(self, current_context_tokens: int) -> bool:
        if not self._cfg.compaction_enabled:
            return False
        limit = max(self.config.provider.context_window, 1)
        threshold = int(limit * self._cfg.compaction_trigger_ratio)
        return (current_context_tokens + _RESPONSE_HEADROOM) >= threshold

    def compact(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        activity: ActivityService | None = None,
    ) -> CompactResult | None:
        """Compact old messages from a session, keeping the last N turns intact."""
        keep_last = self._cfg.compaction_keep_last_turns
        if len(messages) <= keep_last:
            return None

        compactable: list[dict[str, Any]] = []
        keep: list[dict[str, Any]] = []
        # Walk from newest to oldest; newest messages go into keep
        seq_desc = list(reversed(messages))
        for i, msg in enumerate(seq_desc):
            if i < keep_last:
                keep.append(msg)
            else:
                compactable.append(msg)
        compactable.reverse()
        keep.reverse()

        if not compactable:
            return None

        summary = self._summarize(compactable)
        tool_events = self._extract_tool_events(compactable)
        summary_text = summary.strip()
        summary_tokens = max(1, len(summary_text) // _CHARS_PER_TOKEN)

        start_id = compactable[0]["id"]
        end_id = compactable[-1]["id"]

        self.db.add_conversation_compact(
            session_id=session_id,
            start_msg_id=start_id,
            end_msg_id=end_id,
            summary=summary_text,
            tool_events_json=json.dumps(tool_events),
            compacted_count=len(compactable),
        )

        # Mark compacted messages so build_messages skips them
        for msg in compactable:
            self.db.update_message_metadata(msg["id"], {"skip_context": True})

        result = CompactResult(
            session_id=session_id,
            compacted_count=len(compactable),
            summary=summary_text,
            summary_token_count=summary_tokens,
            tool_events=tool_events,
            start_msg_id=start_id,
            end_msg_id=end_id,
        )

        logger.info(
            "Compacted %d messages for session %s (summary=%d tokens)",
            result.compacted_count,
            session_id[:8],
            summary_tokens,
        )

        if activity:
            activity.conversation_compact(
                session_id=session_id,
                turn_range=f"{start_id}..{end_id}",
                summary_token_count=summary_tokens,
            )

        return result

    def get_compact_summary(self, session_id: str) -> str | None:
        compacts = self.db.get_conversation_compacts(session_id)
        if compacts:
            return compacts[-1]["summary"]
        return None

    def _summarize(self, messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        user_prompts: list[str] = []
        decisions: list[str] = []
        instructions: list[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = (msg.get("content") or "").strip()
            meta = json.loads(msg.get("metadata_json") or "{}")

            if role == "user" and content:
                user_prompts.append(content[:300])
            elif role == "assistant":
                if content and content != "[tool call]":
                    instructions.append(content[:400])

            tool_name = meta.get("tool_name", "")
            if tool_name:
                decisions.append(tool_name)

        if user_prompts:
            parts.append("User asked: " + " | ".join(user_prompts[:5]))
            if len(user_prompts) > 5:
                parts.append(f"({len(user_prompts)-5} more user messages)")

        if instructions:
            parts.append("Assistant: " + " | ".join(instructions[:3]))
            if len(instructions) > 3:
                parts.append(f"({len(instructions)-3} more responses)")

        if decisions:
            parts.append("Tools used: " + ", ".join(sorted(set(decisions))))

        return "\n".join(parts) if parts else "(conversation history)"

    @staticmethod
    def _extract_tool_events(messages: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        for msg in messages:
            meta = json.loads(msg.get("metadata_json") or "{}")
            tool_name = meta.get("tool_name", "")
            if tool_name:
                seen.add(tool_name)
        return sorted(seen)
