from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from donovanagent.scheduler.models import ScheduledTask, ScheduledTaskRun
from donovanagent.scheduler.service import SchedulerService
from donovanagent.memory.database import MemoryDatabase


def _make_config() -> Any:
    from donovanagent.config.schema import DonovanAgentConfig
    return DonovanAgentConfig()


def test_scheduled_task_defaults() -> None:
    task = ScheduledTask(name="test-task", prompt="do something", schedule_type="interval", interval_seconds=3600)
    assert task.enabled is True
    assert task.last_status is None
    assert task.id == ""


def test_scheduled_task_with_cron() -> None:
    task = ScheduledTask(name="cron-task", prompt="daily thing", schedule_type="cron", cron_expression="0 9 * * *")
    assert task.schedule_type == "cron"
    assert task.cron_expression == "0 9 * * *"


def test_scheduled_task_onetime() -> None:
    now = datetime.now(timezone.utc).isoformat()
    task = ScheduledTask(name="oneoff", prompt="run once", schedule_type="one_time", run_at=now)
    assert task.schedule_type == "one_time"


def test_scheduled_run() -> None:
    run = ScheduledTaskRun(task_id="t-1", status="completed", result_summary="ok")
    assert run.status == "completed"
    assert run.id == 0
    assert run.result_summary == "ok"


def test_service_add_and_list(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched.db")
    db.initialize()
    service = SchedulerService(db, _make_config())

    task = ScheduledTask(name="test", prompt="hello", schedule_type="interval", interval_seconds=60)
    task_id = service.add_task(task)
    assert task_id is not None

    tasks = service.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].name == "test"


def test_service_remove(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched2.db")
    db.initialize()
    service = SchedulerService(db, _make_config())

    task = ScheduledTask(name="del", prompt="delete me", schedule_type="interval", interval_seconds=60)
    task_id = service.add_task(task)
    assert service.remove_task(task_id) is True
    assert len(service.list_tasks()) == 0


def test_service_pause_resume(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched3.db")
    db.initialize()
    service = SchedulerService(db, _make_config())

    task = ScheduledTask(name="pr", prompt="pause/resume", schedule_type="interval", interval_seconds=60)
    task_id = service.add_task(task)

    assert service.pause_task(task_id) is True
    paused = service.list_tasks()
    assert paused[0].enabled is False

    assert service.resume_task(task_id) is True
    resumed = service.list_tasks()
    assert resumed[0].enabled is True


def test_service_run_now(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched4.db")
    db.initialize()
    service = SchedulerService(db, _make_config())

    results: list[str] = []

    def handler(task: ScheduledTask) -> str:
        results.append(task.name)
        return f"ran {task.name}"

    service.set_run_handler(handler)

    task = ScheduledTask(name="runnow", prompt="run now", schedule_type="interval", interval_seconds=60)
    task_id = service.add_task(task)
    output = service.run_now(task_id)
    assert output == "ran runnow"
    assert len(results) == 1


def test_nonexistent_task_returns_none(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched5.db")
    db.initialize()
    service = SchedulerService(db, _make_config())
    assert service.run_now("nonexistent") is None
    assert service.pause_task("nonexistent") is False
    assert service.resume_task("nonexistent") is False


def test_load_empty(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched6.db")
    db.initialize()
    service = SchedulerService(db, _make_config())
    tasks = service.load()
    assert tasks == []


def test_record_run(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched7.db")
    db.initialize()
    service = SchedulerService(db, _make_config())

    task = ScheduledTask(name="rr", prompt="record", schedule_type="interval", interval_seconds=60)
    task_id = service.add_task(task)
    service.record_run(task_id, "completed", summary="done")
    tasks = service.list_tasks()
    assert tasks[0].last_status == "completed"
    assert tasks[0].last_result_summary == "done"
