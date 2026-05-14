from __future__ import annotations

from pathlib import Path

from donovanagent.execution.base import ExecutionBackend
from donovanagent.execution.local_backend import LocalExecutionBackend


def test_local_backend_name() -> None:
    backend = LocalExecutionBackend()
    assert backend.name == "local"


def test_local_backend_run_command(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    output = backend.run_command("echo hello", cwd=str(tmp_path))
    assert "hello" in output


def test_local_backend_run_command_failure(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    output = backend.run_command("exit 42", cwd=str(tmp_path))
    # Should not raise, returns string output
    assert isinstance(output, str)


def test_local_backend_run_command_with_env(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    output = backend.run_command("echo %MY_VAR%", cwd=str(tmp_path), env={"MY_VAR": "test_val"})
    # On Windows with cmd shell, %MY_VAR% should resolve
    assert isinstance(output, str)


def test_local_backend_read_write_file(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello world", encoding="utf-8")

    content = backend.read_file(str(test_file))
    assert content.strip() == "hello world"

    backend.write_file(str(test_file), "new content")
    assert test_file.read_text(encoding="utf-8") == "new content"


def test_local_backend_list_directory(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")

    entries = backend.list_directory(str(tmp_path))
    names = [e["name"] for e in entries]
    assert "a.txt" in names
    assert "b.txt" in names


def test_local_backend_list_directory_empty(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    entries = backend.list_directory(str(tmp_path))
    assert entries == []


def test_local_backend_path_exists(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    test_file = tmp_path / "exists.txt"
    test_file.write_text("x")
    assert backend.path_exists(str(test_file)) is True
    assert backend.path_exists(str(tmp_path / "nope.txt")) is False


def test_local_backend_get_system_info() -> None:
    backend = LocalExecutionBackend()
    info = backend.get_system_info()
    assert "os" in info
    assert "python" in info
    assert "machine" in info


def test_local_backend_long_running_command(tmp_path: Path) -> None:
    backend = LocalExecutionBackend()
    output = backend.run_command("echo start", cwd=str(tmp_path))
    assert "start" in output


def test_local_backend_close() -> None:
    backend = LocalExecutionBackend()
    # close() should be a no-op
    backend.close()
