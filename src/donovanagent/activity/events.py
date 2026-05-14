from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ActivityStreamEventType:
    agent_message_delta = "agent_message_delta"
    agent_status = "agent_status"
    planning_started = "planning_started"
    planning_delta = "planning_delta"
    planning_finished = "planning_finished"
    checklist_created = "checklist_created"
    checklist_item_started = "checklist_item_started"
    checklist_item_completed = "checklist_item_completed"
    checklist_item_failed = "checklist_item_failed"
    checklist_item_updated = "checklist_item_updated"
    tool_selected = "tool_selected"
    tool_started = "tool_started"
    tool_progress = "tool_progress"
    tool_completed = "tool_completed"
    tool_failed = "tool_failed"
    tool_switch = "tool_switch"
    model_thinking_started = "model_thinking_started"
    model_thinking_delta = "model_thinking_delta"
    model_thinking_finished = "model_thinking_finished"
    subagent_started = "subagent_started"
    subagent_progress = "subagent_progress"
    subagent_completed = "subagent_completed"
    subagent_failed = "subagent_failed"
    memory_recall_started = "memory_recall_started"
    memory_recall_completed = "memory_recall_completed"
    skill_loaded = "skill_loaded"
    skill_learned = "skill_learned"
    skill_draft_created = "skill_draft_created"
    checkpoint_created = "checkpoint_created"
    checkpoint_restored = "checkpoint_restored"
    backend_selected = "backend_selected"
    scheduler_task_started = "scheduler_task_started"
    scheduler_task_completed = "scheduler_task_completed"
    browser_started = "browser_started"
    browser_action = "browser_action"
    browser_completed = "browser_completed"
    task_completed = "task_completed"
    task_failed = "task_failed"


@dataclass
class AgentActivityEvent:
    event_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    event_type: str = ""
    timestamp: str = ""
    message: str = ""
    tool_name: str | None = None
    tool_args_preview: str | None = None
    status: str | None = None
    elapsed_ms: int | None = None
    result_summary: str | None = None
    error: str | None = None
    checklist_item_id: str | None = None
    subagent_id: str | None = None
    provider: str | None = None
    model: str | None = None
    backend: str | None = None
    metadata_json: dict[str, Any] = field(default_factory=dict)
