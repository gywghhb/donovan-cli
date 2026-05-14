from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from donovanagent.checkpoints.models import Checkpoint
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class CheckpointManager:
    """Manages filesystem checkpoints before destructive operations."""

    def __init__(self, config: Any, workspace: str) -> None:
        self.config = config
        self.workspace = workspace
        self._base_dir = Path(workspace) / ".DonovanAgent" / "checkpoints"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoints: dict[str, Checkpoint] = {}

    def _checkpoint_dir(self, cp_id: str) -> Path:
        return self._base_dir / cp_id

    def _is_enabled(self) -> bool:
        cfg = self.config.checkpoints if hasattr(self.config, "checkpoints") else None
        return bool(cfg and cfg.enabled)

    def create(
        self,
        reason: str,
        tool_name: str,
        affected_paths: list[str],
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> Checkpoint | None:
        """Create a checkpoint before a destructive operation."""
        if not self._is_enabled():
            return None

        cp_id = f"cp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        cp_dir = self._checkpoint_dir(cp_id)
        cp_dir.mkdir(parents=True, exist_ok=True)

        # Save copies of affected files
        saved_paths: list[str] = []
        total_size = 0
        for path_str in affected_paths:
            path = Path(path_str)
            if path.exists():
                rel = path.relative_to(self.workspace) if self.workspace else path.name
                backup = cp_dir / "files" / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if path.is_file():
                        shutil.copy2(str(path), str(backup))
                        total_size += path.stat().st_size
                        saved_paths.append(path_str)
                    elif path.is_dir():
                        shutil.copytree(str(path), str(backup), dirs_exist_ok=True)
                        saved_paths.append(path_str)
                except (OSError, shutil.Error) as exc:
                    logger.debug("Checkpoint backup error for %s: %s", path, exc)

        # Git status/diff before
        git_status = ""
        git_diff = ""
        try:
            git_status = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=5,
                cwd=self.workspace,
            ).stdout or ""
            git_diff = subprocess.run(
                ["git", "diff"],
                capture_output=True, text=True, timeout=5,
                cwd=self.workspace,
            ).stdout or ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Write manifest
        now = datetime.now(timezone.utc).isoformat()
        manifest = {
            "checkpoint_id": cp_id,
            "created_at": now,
            "session_id": session_id,
            "turn_id": turn_id,
            "reason": reason,
            "tool_name": tool_name,
            "affected_paths": saved_paths,
            "workspace_path": self.workspace,
            "git_status_before": git_status[:500],
            "git_diff_before": git_diff[:2000],
            "size_bytes": total_size,
        }
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        checkpoint = Checkpoint(
            id=cp_id, reason=reason, tool_name=tool_name,
            affected_paths=saved_paths, workspace_path=self.workspace,
            checkpoint_path=str(cp_dir), size_bytes=total_size,
            created_at=now, git_status_before=git_status[:500],
            git_diff_before=git_diff[:2000],
            session_id=session_id, turn_id=turn_id,
        )
        self._checkpoints[cp_id] = checkpoint

        # Prune old checkpoints
        self._prune()
        return checkpoint

    def list(self) -> list[Checkpoint]:
        """List all checkpoints."""
        if self._checkpoints:
            return sorted(self._checkpoints.values(), key=lambda c: c.created_at, reverse=True)

        # Load from filesystem
        checkpoints: list[Checkpoint] = []
        for d in sorted(self._base_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.json"
            if manifest_path.exists():
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    checkpoints.append(Checkpoint(
                        id=str(data.get("checkpoint_id", d.name)),
                        session_id=str(data.get("session_id") or ""),
                        turn_id=str(data.get("turn_id") or ""),
                        reason=str(data.get("reason", "")),
                        tool_name=str(data.get("tool_name", "")),
                        affected_paths=data.get("affected_paths", []),
                        workspace_path=str(data.get("workspace_path", "")),
                        checkpoint_path=str(d),
                        size_bytes=int(data.get("size_bytes", 0)),
                        created_at=str(data.get("created_at", "")),
                        git_status_before=str(data.get("git_status_before", "")),
                        git_diff_before=str(data.get("git_diff_before", "")),
                    ))
                except (json.JSONDecodeError, OSError) as exc:
                    logger.debug("Error reading checkpoint manifest: %s", exc)
        self._checkpoints = {c.id: c for c in checkpoints}
        return sorted(checkpoints, key=lambda c: c.created_at, reverse=True)

    def get(self, cp_id: str) -> Checkpoint | None:
        """Get a specific checkpoint."""
        checkpoints = self.list()
        for cp in checkpoints:
            if cp.id == cp_id:
                return cp
        return None

    def restore(self, cp_id: str) -> Checkpoint | None:
        """Restore files from a checkpoint. Creates a pre-restore checkpoint."""
        cp = self.get(cp_id)
        if not cp:
            return None

        cp_dir = Path(cp.checkpoint_path)
        files_dir = cp_dir / "files"
        if not files_dir.is_dir():
            logger.error("Checkpoint files not found: %s", files_dir)
            return None

        # Create pre-restore checkpoint
        pre_restore = self.create(
            reason=f"Pre-restore snapshot before rolling back to {cp_id}",
            tool_name="checkpoint_restore",
            affected_paths=cp.affected_paths,
        )

        # Restore files
        for root, dirs, files in os.walk(str(files_dir)):
            for name in files:
                src = Path(root) / name
                rel = src.relative_to(files_dir)
                dst = Path(self.workspace) / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))

        cp.restored_at = datetime.now(timezone.utc).isoformat()
        # Update manifest
        manifest_path = cp_dir / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                data["restored_at"] = cp.restored_at
                manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass

        return pre_restore

    def delete(self, cp_id: str) -> bool:
        """Delete a checkpoint."""
        cp_dir = self._checkpoint_dir(cp_id)
        if cp_dir.is_dir():
            shutil.rmtree(str(cp_dir))
        self._checkpoints.pop(cp_id, None)
        return True

    def diff(self, cp_id: str) -> str | None:
        """Show the diff for a checkpoint."""
        cp = self.get(cp_id)
        if not cp:
            return None
        return cp.git_diff_before

    def _prune(self) -> None:
        """Prune old checkpoints beyond the limit."""
        cfg = self.config.checkpoints if hasattr(self.config, "checkpoints") else None
        if not cfg or not cfg.auto_prune:
            return
        max_cp = cfg.max_checkpoints or 100

        all_cps = self.list()
        if len(all_cps) <= max_cp:
            return

        to_delete = sorted(all_cps, key=lambda c: c.created_at)[:-max_cp]
        for cp in to_delete:
            self.delete(cp.id)
