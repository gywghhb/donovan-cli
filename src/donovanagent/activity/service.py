from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from donovanagent.activity.events import AgentActivityEvent
from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class ActivityService:
    """Manages activity event creation, storage, and callback dispatch.

    Events are buffered in memory and flushed to DB in batches to reduce
    SQLite connection churn during tool-heavy turns.
    """

    def __init__(self, db: MemoryDatabase | None = None, config: Any = None) -> None:
        self.db = db
        self.config = config
        self._listeners: list[callable] = []
        self._event_count = 0
        self._current_tool_start: int | None = None
        self._event_buffer: list[dict[str, Any]] = []
        self._buffer_size = 0

    def add_listener(self, callback: callable) -> None:
        self._listeners.append(callback)

    def _should_save(self) -> bool:
        cfg = self.config.activity_stream if self.config and hasattr(self.config, "activity_stream") else None
        if cfg and hasattr(cfg, "save_events"):
            return bool(cfg.save_events)
        return True

    def _is_enabled(self) -> bool:
        cfg = self.config.activity_stream if self.config and hasattr(self.config, "activity_stream") else None
        if cfg and hasattr(cfg, "enabled"):
            return bool(cfg.enabled)
        return True

    def emit(self, event_type: str, **kwargs: Any) -> AgentActivityEvent:
        if not self._is_enabled():
            event = AgentActivityEvent(event_type=event_type)
            return event

        event = AgentActivityEvent(
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in kwargs.items() if v is not None},
        )
        self._event_count += 1

        # Persist to SQLite (buffered — flushed periodically or on flush())
        if self._should_save() and self.db is not None and hasattr(self.db, 'add_activity_event'):
            self._event_buffer.append({
                "session_id": event.session_id,
                "turn_id": event.turn_id,
                "task_id": event.task_id,
                "event_type": event.event_type,
                "timestamp": event.timestamp,
                "message": event.message,
                "tool_name": event.tool_name,
                "tool_args_json": event.tool_args_preview,
                "tool_result_summary": event.result_summary,
                "elapsed_ms": event.elapsed_ms,
                "status": event.status,
                "model": event.model,
                "provider": event.provider,
                "backend": event.backend,
                "subagent_id": event.subagent_id,
                "checklist_item_id": event.checklist_item_id,
                "metadata_json": json.dumps(event.metadata_json, ensure_ascii=False) if event.metadata_json else "{}",
            })
            self._buffer_size += 1
            # Flush every 100 events to reduce SQLite write pressure
            if self._buffer_size >= 100:
                self._flush_buffer()

        # Notify listeners
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as exc:
                logger.debug("Activity listener error: %s", exc)

        return event

    def _flush_buffer(self) -> None:
        """Flush buffered events to SQLite in a single connection."""
        if not self._event_buffer or self.db is None:
            return
        try:
            self.db.batch_add_activity_events(self._event_buffer)
        except Exception as exc:
            logger.debug("Failed to flush activity event buffer: %s", exc)
        self._event_buffer = []
        self._buffer_size = 0

    def flush(self) -> None:
        """Explicitly flush buffered events. Call at end of each turn."""
        self._flush_buffer()

    def tool_started(self, tool_name: str, args: dict[str, Any] | None = None, **kwargs: Any) -> AgentActivityEvent:
        self._current_tool_start = 0  # Reset timer
        from donovanagent.activity.renderer import _preview_args
        cfg = self.config.activity_stream if self.config and hasattr(self.config, "activity_stream") else None
        args_mode = cfg.show_tool_args if cfg else "preview"
        return self.emit(
            "tool_started",
            tool_name=tool_name,
            tool_args_preview=_preview_args(tool_name, args, mode=args_mode),
            status="running",
            elapsed_ms=None,
            **kwargs,
        )

    def tool_completed(self, tool_name: str, result_summary: str = "", elapsed_ms: int = 0, **kwargs: Any) -> AgentActivityEvent:
        return self.emit(
            "tool_completed",
            tool_name=tool_name,
            result_summary=result_summary,
            status="completed",
            elapsed_ms=elapsed_ms,
            **kwargs,
        )

    def tool_failed(self, tool_name: str, error: str = "", elapsed_ms: int = 0, **kwargs: Any) -> AgentActivityEvent:
        return self.emit(
            "tool_failed",
            tool_name=tool_name,
            error=error,
            status="failed",
            elapsed_ms=elapsed_ms,
            **kwargs,
        )

    def tool_switch(self, from_tool: str, to_tool: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit(
            "tool_switch",
            message=f"Switching from {from_tool} to {to_tool}",
            tool_name=to_tool,
            **kwargs,
        )

    def agent_status(self, message: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("agent_status", message=message, **kwargs)

    def checklist_item_started(self, item_id: str, title: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("checklist_item_started", message=title, checklist_item_id=item_id, status="active", **kwargs)

    def checklist_item_completed(self, item_id: str, title: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("checklist_item_completed", message=title, checklist_item_id=item_id, status="completed", **kwargs)

    def checklist_item_failed(self, item_id: str, title: str, error: str = "", **kwargs: Any) -> AgentActivityEvent:
        return self.emit("checklist_item_failed", message=title, checklist_item_id=item_id, status="failed", error=error, **kwargs)

    def subagent_started(self, subagent_id: str, role: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("subagent_started", subagent_id=subagent_id, message=f"{role} started", status="running", **kwargs)

    def subagent_completed(self, subagent_id: str, role: str, result: str = "", **kwargs: Any) -> AgentActivityEvent:
        return self.emit("subagent_completed", subagent_id=subagent_id, message=f"{role} completed", result_summary=result, status="completed", **kwargs)

    def memory_recall_started(self, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("memory_recall_started", message="Searching previous sessions...", **kwargs)

    def memory_recall_completed(self, count: int, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("memory_recall_completed", message=f"Found {count} relevant memories", **kwargs)

    def checkpoint_created(self, checkpoint_id: str, affected: list[str], **kwargs: Any) -> AgentActivityEvent:
        return self.emit("checkpoint_created", message=f"Checkpoint created: {checkpoint_id}", tool_name="checkpoint", result_summary=f"Affected files: {len(affected)}", **kwargs)

    def skill_loaded(self, name: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("skill_loaded", message=f"Loaded skill: {name}", **kwargs)

    def skill_learned(self, name: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("skill_learned", message=f"Learned new skill: {name}", **kwargs)

    def skill_draft_created(self, name: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("skill_draft_created", message=f"Draft skill created: {name} (review with /skill approve)", **kwargs)

    def task_completed(self, message: str = "Task completed.", **kwargs: Any) -> AgentActivityEvent:
        return self.emit("task_completed", message=message, status="completed", **kwargs)

    def task_failed(self, error: str = "", **kwargs: Any) -> AgentActivityEvent:
        return self.emit("task_failed", message="Task failed", error=error, status="failed", **kwargs)

    def planning_started(self, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("planning_started", message="Creating plan...", **kwargs)

    def planning_finished(self, item_count: int = 0, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("planning_finished", message=f"Plan created with {item_count} steps.", **kwargs)

    def thinking_started(self, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("model_thinking_started", message="Thinking...", **kwargs)

    def thinking_finished(self, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("model_thinking_finished", message="Ready.", **kwargs)

    def conversation_compact(
        self,
        session_id: str,
        turn_range: str,
        summary_token_count: int,
        **kwargs: Any,
    ) -> AgentActivityEvent:
        return self.emit(
            "conversation_compact",
            message=f"Compacted {turn_range} ({summary_token_count} tokens)",
            session_id=session_id,
            turn_range=turn_range,
            summary_token_count=summary_token_count,
            **kwargs,
        )

    def backend_selected(self, backend: str, **kwargs: Any) -> AgentActivityEvent:
        return self.emit("backend_selected", message=f"Backend: {backend}", backend=backend, **kwargs)

    @property
    def count(self) -> int:
        return self._event_count
