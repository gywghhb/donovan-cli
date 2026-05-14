from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.security.permissions import PathPermissions


@dataclass(frozen=True)
class Sandbox:
    """Permission facade for local tool execution.

    DonovanAgent does not claim an OS sandbox. This class centralizes path checks
    so tools consistently respect the configured approved and blocked paths.
    """

    config: DonovanAgentConfig

    @property
    def paths(self) -> PathPermissions:
        return PathPermissions(self.config)

    def resolve_read(self, path: str | Path, base: str | Path | None = None) -> Path:
        return self.paths.require_read(path, base)

    def resolve_write(self, path: str | Path, base: str | Path | None = None) -> Path:
        return self.paths.require_write(path, base)
