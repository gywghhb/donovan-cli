from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from donovanagent.activity.events import AgentActivityEvent, ActivityStreamEventType
from donovanagent.activity.service import ActivityService
from donovanagent.memory.database import MemoryDatabase


def test_event_type_constants() -> None:
    assert ActivityStreamEventType.tool_started == "tool_started"
    assert ActivityStreamEventType.tool_completed == "tool_completed"
    assert ActivityStreamEventType.tool_failed == "tool_failed"
    assert ActivityStreamEventType.tool_switch == "tool_switch"
    assert ActivityStreamEventType.checkpoint_created == "checkpoint_created"
    assert ActivityStreamEventType.skill_loaded == "skill_loaded"
    assert ActivityStreamEventType.skill_learned == "skill_learned"
    assert ActivityStreamEventType.planning_started == "planning_started"
    assert ActivityStreamEventType.planning_finished == "planning_finished"
    assert ActivityStreamEventType.model_thinking_started == "model_thinking_started"
    assert ActivityStreamEventType.model_thinking_finished == "model_thinking_finished"
    assert ActivityStreamEventType.backend_selected == "backend_selected"
    assert ActivityStreamEventType.task_completed == "task_completed"
    assert ActivityStreamEventType.task_failed == "task_failed"


def test_event_dataclass_defaults() -> None:
    event = AgentActivityEvent(
        event_id="test-1",
        session_id="sess-1",
        event_type="tool_started",
    )
    assert event.turn_id is None
    assert event.task_id is None
    assert event.tool_name is None
    assert event.status is None
    assert event.elapsed_ms is None
    assert event.error is None
    assert event.provider is None
    assert event.backend is None


def test_event_with_all_fields() -> None:
    event = AgentActivityEvent(
        event_id="e-1",
        session_id="s-1",
        turn_id="t-1",
        task_id="task-1",
        event_type="tool_completed",
        timestamp=datetime.now(timezone.utc).isoformat(),
        message="Done",
        tool_name="bash",
        tool_args_preview="echo hello",
        status="success",
        elapsed_ms=1500,
        result_summary="hello",
        provider="anthropic",
        model="anthropic-model",
        backend="local",
    )
    assert event.event_id == "e-1"
    assert event.tool_args_preview == "echo hello"
    assert event.elapsed_ms == 1500


def test_activity_service_emit(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "activity.db")
    db.initialize()
    service = ActivityService(db)
    events: list[AgentActivityEvent] = []

    def listener(e: AgentActivityEvent) -> None:
        events.append(e)

    service.add_listener(listener)
    event = service.emit("test_event", session_id="sess-1", message="hello")
    assert len(events) >= 1
    assert event.event_type == "test_event"


def test_activity_service_emit_without_db(tmp_path: Path) -> None:
    """Emit should work even without a database."""
    service = ActivityService()
    event = service.emit("test_event", session_id="sess-1", message="hello")
    assert event.event_type == "test_event"
    assert event.message == "hello"


def test_convenience_methods(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "conv.db")
    db.initialize()
    service = ActivityService(db)
    events: list[AgentActivityEvent] = []
    service.add_listener(lambda e: events.append(e))

    service.tool_started("bash", args={"cmd": "echo hi"})
    service.tool_completed("bash", result_summary="hi", elapsed_ms=100)
    service.tool_failed("bash", error="oops")
    service.agent_status("thinking")
    service.checkpoint_created("cp-1", affected=["file.txt"])
    service.skill_loaded("test-skill")
    service.backend_selected("docker")
    service.task_completed()
    service.task_failed(error="fail")
    service.planning_started()
    service.planning_finished(item_count=3)
    service.thinking_started()
    service.thinking_finished()

    event_types = [e.event_type for e in events]
    assert "tool_started" in event_types
    assert "tool_completed" in event_types
    assert "tool_failed" in event_types
    assert "agent_status" in event_types
    assert "checkpoint_created" in event_types
    assert "skill_loaded" in event_types
    assert "backend_selected" in event_types
    assert "task_completed" in event_types
    assert "task_failed" in event_types
    assert "planning_started" in event_types
    assert "planning_finished" in event_types
    assert "model_thinking_started" in event_types
    assert "model_thinking_finished" in event_types


def test_event_count(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "count.db")
    db.initialize()
    service = ActivityService(db)
    service.emit("e1")
    service.emit("e2")
    service.emit("e3")
    assert service.count == 3
