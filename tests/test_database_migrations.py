from __future__ import annotations

from pathlib import Path

from donovanagent.memory.database import MemoryDatabase


def _table_exists(db: MemoryDatabase, table_name: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None


def test_migration_applies_cleanly(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "migrate.db")
    db.initialize()

    with db.connect() as conn:
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        assert row is not None
        assert row[0] >= 1


def test_memory_table_exists(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "memories.db")
    db.initialize()
    assert _table_exists(db, "memories")


def test_activity_events_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "activity_tbl.db")
    db.initialize()
    assert _table_exists(db, "agent_activity_events")


def test_skill_drafts_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "drafts.db")
    db.initialize()
    assert _table_exists(db, "skill_drafts")


def test_scheduled_tasks_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "sched_tbl.db")
    db.initialize()
    assert _table_exists(db, "scheduled_tasks")


def test_plans_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "plans.db")
    db.initialize()
    assert _table_exists(db, "plans")


def test_checkpoints_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "cp_tbl.db")
    db.initialize()
    assert _table_exists(db, "checkpoints")


def test_project_context_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "ctx.db")
    db.initialize()
    assert _table_exists(db, "project_context")


def test_subagents_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "subs.db")
    db.initialize()
    assert _table_exists(db, "subagents")


def test_browser_sessions_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "brows.db")
    db.initialize()
    assert _table_exists(db, "browser_sessions")


def test_idempotent_migration(tmp_path: Path) -> None:
    """Running initialize() twice should not cause errors."""
    db = MemoryDatabase(tmp_path / "idempotent.db")
    db.initialize()
    db.initialize()  # Second call should be safe


def test_schema_version_increments(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "version.db")
    db.initialize()

    with db.connect() as conn:
        version = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        assert version is not None
        assert version[0] >= 2


def test_add_activity_event(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "aev.db")
    db.initialize()
    event_id = db.add_activity_event(
        session_id="sess-1",
        event_type="tool_started",
        tool_name="bash",
    )
    assert event_id > 0


def test_add_memory_record(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "memr.db")
    db.initialize()
    mem_id = db.add_memory_record(
        memory_type="user_preference",
        title="test",
        content="test memory content",
        source="user",
    )
    assert mem_id > 0


def test_search_memories(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "searchm.db")
    db.initialize()
    db.add_memory_record(
        memory_type="user_preference",
        title="dark mode",
        content="user prefers dark mode",
        source="user",
    )
    results = db.search_memories("dark")
    assert len(results) >= 1
    assert any("dark" in r.get("content", "") for r in results)


def test_add_scheduled_task(tmp_path: Path) -> None:
    from donovanagent.scheduler.models import ScheduledTask

    db = MemoryDatabase(tmp_path / "scht.db")
    db.initialize()
    task = ScheduledTask(name="test", prompt="hello", schedule_type="interval", interval_seconds=60)
    db.add_scheduled_task(task)

    tasks = db.list_scheduled_tasks()
    assert len(tasks) >= 1


def test_fts_available(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "fts_check.db")
    db.initialize()
    # FTS5 might not be available on all Python builds
    assert isinstance(db.fts_available(), bool)


def test_conversation_compacts_table(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "conv_compact.db")
    db.initialize()
    assert _table_exists(db, "conversation_compacts")
    cid = db.add_conversation_compact(
        session_id="sess-1",
        start_msg_id=1,
        end_msg_id=10,
        summary="Summarized conversation history",
        tool_events_json='["read_file"]',
        compacted_count=10,
    )
    assert cid > 0
    compacts = db.get_conversation_compacts("sess-1")
    assert len(compacts) == 1
    assert compacts[0]["summary"] == "Summarized conversation history"
    assert compacts[0]["compacted_count"] == 10
