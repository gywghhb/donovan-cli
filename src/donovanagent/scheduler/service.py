from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from donovanagent.scheduler.models import ScheduledTask, ScheduledTaskRun
from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class SchedulerService:
    """Manages scheduled recurring agent tasks."""

    def __init__(self, db: MemoryDatabase, config: Any) -> None:
        self.db = db
        self.config = config
        self._tasks: dict[str, ScheduledTask] = {}
        self._on_run: Callable[[ScheduledTask], str] | None = None

    def set_run_handler(self, handler: Callable[[ScheduledTask], str]) -> None:
        self._on_run = handler

    def load(self) -> list[ScheduledTask]:
        """Load all scheduled tasks from SQLite."""
        self._tasks.clear()
        if not hasattr(self.db, 'list_scheduled_tasks'):
            return []
        try:
            rows = self.db.list_scheduled_tasks()
            for row in rows:
                task = ScheduledTask(
                    id=str(row["id"]),
                    name=str(row.get("name", "")),
                    prompt=str(row.get("prompt", "")),
                    schedule_type=str(row.get("schedule_type", "interval")),
                    cron_expression=str(row.get("cron_expression") or None),
                    interval_seconds=int(row["interval_seconds"]) if row.get("interval_seconds") else None,
                    run_at=str(row.get("run_at") or None),
                    timezone=str(row.get("timezone", "UTC")),
                    workspace_path=str(row.get("workspace_path") or None),
                    provider=str(row.get("provider") or None),
                    model=str(row.get("model") or None),
                    execution_backend=str(row.get("execution_backend", "local")),
                    enabled=bool(row.get("enabled", True)),
                    created_at=str(row.get("created_at", "")),
                    updated_at=str(row.get("updated_at", "")),
                    last_run_at=str(row.get("last_run_at") or None),
                    next_run_at=str(row.get("next_run_at") or None),
                    last_status=str(row.get("last_status") or None),
                    last_result_summary=str(row.get("last_result_summary") or None),
                )
                self._tasks[task.id] = task
        except Exception as exc:
            logger.debug("Failed to load scheduled tasks: %s", exc)
        return list(self._tasks.values())

    def add_task(self, task: ScheduledTask) -> str:
        """Add a new scheduled task."""
        if not task.id:
            task.id = f"sch_{uuid.uuid4().hex[:12]}"
        task.created_at = datetime.now(timezone.utc).isoformat()
        task.updated_at = task.created_at
        task.next_run_at = self._calculate_next_run(task)
        self._tasks[task.id] = task
        if hasattr(self.db, 'add_scheduled_task'):
            try:
                self.db.add_scheduled_task(task)
            except Exception as exc:
                logger.error("Failed to persist scheduled task: %s", exc)
        return task.id

    def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
        if hasattr(self.db, 'delete_scheduled_task'):
            try:
                self.db.delete_scheduled_task(task_id)
            except Exception as exc:
                logger.error("Failed to delete scheduled task: %s", exc)
        return True

    def pause_task(self, task_id: str) -> bool:
        """Pause a scheduled task."""
        task = self._tasks.get(task_id)
        if task:
            task.enabled = False
            task.updated_at = datetime.now(timezone.utc).isoformat()
            if hasattr(self.db, 'update_scheduled_task'):
                try:
                    self.db.update_scheduled_task(task)
                except Exception:
                    pass
            return True
        return False

    def resume_task(self, task_id: str) -> bool:
        """Resume a paused task."""
        task = self._tasks.get(task_id)
        if task:
            task.enabled = True
            task.next_run_at = self._calculate_next_run(task)
            task.updated_at = datetime.now(timezone.utc).isoformat()
            if hasattr(self.db, 'update_scheduled_task'):
                try:
                    self.db.update_scheduled_task(task)
                except Exception:
                    pass
            return True
        return False

    def run_now(self, task_id: str) -> str | None:
        """Execute a scheduled task immediately."""
        task = self._tasks.get(task_id)
        if not task:
            return None
        if self._on_run:
            return self._on_run(task)
        return None

    def check_due(self) -> list[ScheduledTask]:
        """Check for tasks that are due to run."""
        now = datetime.now(timezone.utc)
        due: list[ScheduledTask] = []
        for task in self._tasks.values():
            if not task.enabled:
                continue
            if not task.next_run_at:
                continue
            try:
                next_time = datetime.fromisoformat(task.next_run_at)
                if now >= next_time:
                    due.append(task)
            except (ValueError, TypeError):
                continue
        return due

    def record_run(
        self, task_id: str, status: str, summary: str = "",
        session_id: str | None = None, error: str | None = None,
    ) -> None:
        """Record the result of a task execution."""
        task = self._tasks.get(task_id)
        if not task:
            return
        task.last_run_at = datetime.now(timezone.utc).isoformat()
        task.last_status = status
        task.last_result_summary = summary
        task.next_run_at = self._calculate_next_run(task)
        task.updated_at = task.last_run_at

        if hasattr(self.db, 'add_scheduled_task_run'):
            try:
                self.db.add_scheduled_task_run(
                    task_id=task_id, status=status, result_summary=summary,
                    session_id=session_id, error=error,
                )
            except Exception as exc:
                logger.debug("Failed to record scheduled run: %s", exc)

    def _calculate_next_run(self, task: ScheduledTask) -> str | None:
        """Calculate the next run time based on schedule type."""
        now = datetime.now(timezone.utc)
        if task.schedule_type == "interval" and task.interval_seconds:
            next_time = now + timedelta(seconds=task.interval_seconds)
            return next_time.isoformat()
        elif task.schedule_type == "one_time" and task.run_at:
            try:
                run_time = datetime.fromisoformat(task.run_at)
                if run_time > now:
                    return run_time.isoformat()
            except ValueError:
                pass
            return None
        # Cron â€” approximate: run every 24h from now
        if task.schedule_type == "cron":
            return (now + timedelta(hours=24)).isoformat()
        return None

    def list_tasks(self) -> list[ScheduledTask]:
        """List all scheduled tasks."""
        if not self._tasks:
            self.load()
        return list(self._tasks.values())

    def close(self) -> None:
        self._tasks.clear()
