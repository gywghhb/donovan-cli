from __future__ import annotations

from donovanagent.utils.shell import resolve_shell


def test_shell_resolver_returns_backend() -> None:
    shell = resolve_shell()
    assert shell.kind
    assert shell.executable
    assert shell.args_for("echo hi")
