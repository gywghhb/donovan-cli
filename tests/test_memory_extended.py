from __future__ import annotations

from pathlib import Path

from donovanagent.memory.database import MemoryDatabase
from donovanagent.memory.manager import MemoryManager
from donovanagent.memory.project_context import detect_project_context, save_project_context
from donovanagent.memory.summaries import generate_session_summary


def _make_config() -> object:
    from donovanagent.config.schema import DonovanAgentConfig
    return DonovanAgentConfig()


def test_memory_manager_add_and_search(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "mm.db")
    db.initialize()
    config = _make_config()
    mm = MemoryManager(db, config)

    mm.add_memory(
        memory_type="user_preference",
        title="test pref",
        content="user likes dark mode",
        source="user",
    )
    results = mm.search("dark")
    assert len(results) >= 1
    assert any("dark" in r.get("content", "").lower() for r in results)


def test_memory_delete_direct(tmp_path: Path) -> None:
    """Test delete via db directly to isolate from MemoryManager wrapper issues."""
    db = MemoryDatabase(tmp_path / "mm_del.db")
    db.initialize()

    mem_id = db.add_memory_record(
        memory_type="user_preference",
        title="delete test",
        content="temporary memory to delete",
        source="user",
    )
    assert mem_id > 0

    db.delete_memory_record(mem_id)
    results = db.search_memories("temporary")
    assert len(results) == 0


def test_memory_manager_delete_via_mm(tmp_path: Path) -> None:
    """Test MemoryManager.delete with direct db insertion."""
    db = MemoryDatabase(tmp_path / "mm_delete.db")
    db.initialize()
    config = _make_config()
    mm = MemoryManager(db, config)

    # Insert a memory record manually
    mem_id = db.add_memory_record(
        memory_type="user_preference",
        title="delete via mm",
        content="memory to delete via manager",
        source="user",
    )
    assert mem_id > 0
    # Use MemoryManager's delete
    assert mm.delete(mem_id) is True


def test_memory_manager_disabled(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "mm3.db")
    db.initialize()
    config = _make_config()
    config.memory.enabled = False
    mm = MemoryManager(db, config)

    result = mm.add_memory(
        memory_type="user_preference",
        title="should not save",
        content="this should not be saved",
        source="user",
    )
    assert result is None
    assert mm.search("should") == []


def test_session_summary_generation(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "summary.db")
    db.initialize()
    session_id = db.create_session(str(tmp_path), "test", "model")

    db.add_message(session_id, "user", "fix the bug in login")
    db.add_message(session_id, "assistant", "I found the issue and fixed it")
    db.add_message(session_id, "tool", "")

    msgs = db.recent_messages(session_id, limit=10)
    summary = generate_session_summary(db, session_id, msgs)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_project_context_detection_python(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'", encoding="utf-8")
    (tmp_path / "src").mkdir()

    ctx = detect_project_context(str(tmp_path))
    assert ctx["language"] == "Python"
    assert ctx["project_type"] == "Python package"
    assert "src" in ctx["important_folders"]
    assert "pytest" in " ".join(ctx["test_commands"]).lower()


def test_project_context_detection_node(tmp_path: Path) -> None:
    import json
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "^14.0.0"}}), encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    ctx = detect_project_context(str(tmp_path))
    assert ctx["language"] == "JavaScript/Node.js"
    assert ctx["framework"] == "Next.js"
    assert "src" in ctx["important_folders"]
    assert "tests" in ctx["important_folders"]


def test_project_context_detection_rust(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'", encoding="utf-8")

    ctx = detect_project_context(str(tmp_path))
    assert ctx["language"] == "Rust"
    assert "cargo test" in ctx["test_commands"]


def test_project_context_detection_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module test", encoding="utf-8")

    ctx = detect_project_context(str(tmp_path))
    assert ctx["language"] == "Go"


def test_project_context_empty_directory(tmp_path: Path) -> None:
    ctx = detect_project_context(str(tmp_path))
    assert ctx["language"] is None
    assert ctx["project_type"] is None


def test_save_project_context(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "ctx_save.db")
    db.initialize()
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")

    ctx = save_project_context(db, str(tmp_path))
    assert ctx["language"] == "Python"


def test_session_summarize_via_manager(tmp_path: Path) -> None:
    db = MemoryDatabase(tmp_path / "summ_mgr.db")
    db.initialize()
    config = _make_config()
    config.memory.auto_summarize_sessions = True
    mm = MemoryManager(db, config)

    session_id = db.create_session(str(tmp_path), "test", "model")
    db.add_message(session_id, "user", "hello")
    db.add_message(session_id, "assistant", "hi there!")

    msgs = db.recent_messages(session_id, limit=10)
    summary = mm.summarize_session(session_id, msgs)
    assert summary is not None
    assert "hello" in summary or "hi" in summary
