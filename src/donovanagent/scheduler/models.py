from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScheduledTask:
    id: str = ""
    name: str = ""
    prompt: str = ""
    schedule_type: str = "interval"  # cron, interval, one_time
    cron_expression: str | None = None
    interval_seconds: int | None = None
    run_at: str | None = None
    timezone: str = "UTC"
    workspace_path: str | None = None
    provider: str | None = None
    model: str | None = None
    execution_backend: str = "local"
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_status: str | None = None
    last_result_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduledTaskRun:
    id: int = 0
    task_id: str = ""
    started_at: str = ""
    finished_at: str | None = None
    status: str | None = None  # running, completed, failed
    result_summary: str | None = None
    session_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
