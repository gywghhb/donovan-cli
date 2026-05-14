from __future__ import annotations

from pathlib import Path

import pytest

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.security.permissions import PathPermissions
from donovanagent.utils.errors import PermissionDenied


def test_approved_path_allows_child(tmp_path: Path) -> None:
    config = DonovanAgentConfig()
    config.app.default_workspace = str(tmp_path)
    config.security.approved_paths = [str(tmp_path)]
    allowed = PathPermissions(config).require_read("child.txt")
    assert allowed == tmp_path / "child.txt"


def test_outside_path_denied(tmp_path: Path) -> None:
    config = DonovanAgentConfig()
    config.app.default_workspace = str(tmp_path / "work")
    config.security.approved_paths = [str(tmp_path / "work")]
    with pytest.raises(PermissionDenied):
        PathPermissions(config).require_read(tmp_path / "outside.txt")


def test_readonly_write_denied(tmp_path: Path) -> None:
    config = DonovanAgentConfig()
    config.app.default_workspace = str(tmp_path)
    config.security.approved_paths = [str(tmp_path)]
    config.app.permission_mode = "readonly"
    with pytest.raises(PermissionDenied):
        PathPermissions(config).require_write("x.txt")
