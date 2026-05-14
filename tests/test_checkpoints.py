from __future__ import annotations

from pathlib import Path
from typing import Any

from donovanagent.checkpoints.models import Checkpoint
from donovanagent.checkpoints.manager import CheckpointManager


def _make_config(workspace: str) -> Any:
    from donovanagent.config.schema import DonovanAgentConfig, CheckpointsConfig
    config = DonovanAgentConfig()
    config.checkpoints = CheckpointsConfig(enabled=True, max_checkpoints=10)
    config.app.default_workspace = workspace
    return config


def test_checkpoint_dataclass() -> None:
    cp = Checkpoint(
        id="cp-1",
        reason="test",
        tool_name="bash",
        affected_paths=["file.txt"],
        created_at="2025-01-01T00:00:00",
    )
    assert cp.id == "cp-1"
    assert cp.restored_at is None
    assert cp.git_status_before is None


def test_checkpoint_manager_create_and_list(tmp_path: Path) -> None:
    config = _make_config(str(tmp_path))
    manager = CheckpointManager(config, str(tmp_path))

    test_file = tmp_path / "test.txt"
    test_file.write_text("original", encoding="utf-8")

    cp = manager.create(reason="testing", tool_name="write", affected_paths=[str(test_file)])
    assert cp is not None
    assert cp.reason == "testing"

    cps = manager.list()
    assert len(cps) >= 1
    assert any(c.id == cp.id for c in cps)


def test_checkpoint_get(tmp_path: Path) -> None:
    config = _make_config(str(tmp_path))
    manager = CheckpointManager(config, str(tmp_path))

    test_file = tmp_path / "test.txt"
    test_file.write_text("original", encoding="utf-8")
    cp = manager.create(reason="get-test", tool_name="write", affected_paths=[str(test_file)])

    fetched = manager.get(cp.id)
    assert fetched is not None
    assert fetched.id == cp.id

    assert manager.get("nonexistent") is None


def test_checkpoint_restore(tmp_path: Path) -> None:
    config = _make_config(str(tmp_path))
    manager = CheckpointManager(config, str(tmp_path))

    test_file = tmp_path / "test.txt"
    test_file.write_text("original", encoding="utf-8")

    cp = manager.create(reason="restore-test", tool_name="write", affected_paths=[str(test_file)])
    test_file.write_text("modified", encoding="utf-8")

    pre = manager.restore(cp.id)
    assert pre is not None  # pre-restore checkpoint created
    assert test_file.read_text(encoding="utf-8") == "original"


def test_checkpoint_delete(tmp_path: Path) -> None:
    config = _make_config(str(tmp_path))
    manager = CheckpointManager(config, str(tmp_path))

    test_file = tmp_path / "test.txt"
    test_file.write_text("original", encoding="utf-8")
    cp = manager.create(reason="del-test", tool_name="write", affected_paths=[str(test_file)])

    manager.delete(cp.id)
    assert manager.get(cp.id) is None


def test_checkpoint_diff(tmp_path: Path) -> None:
    config = _make_config(str(tmp_path))
    manager = CheckpointManager(config, str(tmp_path))

    test_file = tmp_path / "test.txt"
    test_file.write_text("original", encoding="utf-8")
    cp = manager.create(reason="diff-test", tool_name="write", affected_paths=[str(test_file)])

    diff = manager.diff(cp.id)
    assert diff is not None  # may be empty if no git repo


def test_checkpoint_preserves_files(tmp_path: Path) -> None:
    config = _make_config(str(tmp_path))
    manager = CheckpointManager(config, str(tmp_path))

    test_file = tmp_path / "preserve.txt"
    test_file.write_text("preserve me", encoding="utf-8")

    cp = manager.create(reason="preserve", tool_name="write", affected_paths=[str(test_file)])
    assert cp.checkpoint_path != ""

    checkpoint_dir = Path(cp.checkpoint_path)
    assert checkpoint_dir.is_dir()
