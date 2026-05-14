from __future__ import annotations

import os
from pathlib import Path

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.utils.errors import PermissionDenied


def _normcase(path: Path) -> str:
    return os.path.normcase(str(path))


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


class PathPermissions:
    def __init__(self, config: DonovanAgentConfig) -> None:
        self.config = config

    def resolve(self, path: str | Path, base: str | Path | None = None) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            root = Path(base or self.config.app.default_workspace).expanduser()
            candidate = root / candidate
        return candidate.resolve(strict=False)

    def approved_roots(self) -> list[Path]:
        roots: list[Path] = []
        for raw in self.config.security.approved_paths:
            try:
                roots.append(Path(raw).expanduser().resolve(strict=False))
            except OSError:
                continue
        return roots

    def blocked_roots(self) -> list[Path]:
        roots: list[Path] = []
        for raw in self.config.security.blocked_paths:
            try:
                roots.append(Path(raw).expanduser().resolve(strict=False))
            except OSError:
                continue
        return roots

    def is_approved(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(is_relative_to(resolved, root) for root in self.approved_roots())

    def is_blocked(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        for root in self.blocked_roots():
            if _normcase(root) == _normcase(Path(root.anchor)):
                if _normcase(resolved) == _normcase(root):
                    return True
                continue
            if is_relative_to(resolved, root):
                return True
        return False

    def require_read(self, path: str | Path, base: str | Path | None = None) -> Path:
        resolved = self.resolve(path, base)
        if not self.is_approved(resolved):
            raise PermissionDenied(f"{resolved} is outside approved DonovanAgent paths")
        return resolved

    def require_write(self, path: str | Path, base: str | Path | None = None) -> Path:
        resolved = self.resolve(path, base)
        if self.config.app.permission_mode == "readonly":
            raise PermissionDenied("readonly mode does not allow file writes")
        if not self.is_approved(resolved):
            raise PermissionDenied(f"{resolved} is outside approved DonovanAgent paths")
        if self.is_blocked(resolved) and self.config.app.permission_mode != "full_autonomy":
            raise PermissionDenied(f"{resolved} is inside a blocked system path")
        return resolved

    def require_cwd(self, path: str | Path | None) -> Path:
        resolved = self.resolve(path or self.config.app.default_workspace)
        if not self.is_approved(resolved):
            raise PermissionDenied(f"Working directory {resolved} is outside approved paths")
        return resolved
