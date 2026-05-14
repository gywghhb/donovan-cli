from __future__ import annotations

from pathlib import Path

from donovanagent.memory.database import MemoryDatabase


def test_memory_database_init_and_messages(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "memory.db")
    db.initialize()
    session_id = db.create_session(str(tmp_path), "ollama", "model")
    message_id = db.add_message(session_id, "user", "hello searchable world")
    assert message_id > 0
    recent = db.recent_messages(session_id)
    assert recent[0]["content"] == "hello searchable world"


def test_audit_log(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "memory.db")
    db.initialize()
    audit_id = db.add_audit("test", "pytest", details={"ok": True})
    assert audit_id > 0


def test_learned_skills(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "memory.db")
    db.initialize()
    skill_id = db.upsert_skill(
        "Test workflow",
        "A reusable test workflow",
        "When testing, run pytest and inspect failures.",
    )
    assert skill_id > 0
    skills = db.search_skills("testing pytest", limit=3)
    assert skills[0]["name"] == "Test workflow"
    db.mark_skills_used([skill_id])
    listed = db.list_skills()
    assert listed[0]["uses"] == 1
