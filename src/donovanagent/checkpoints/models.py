from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Checkpoint:
    id: str = ""
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    reason: str = ""
    tool_name: str = ""
    affected_paths: list[str] = field(default_factory=list)
    workspace_path: str | None = None
    provider: str | None = None
    model: str | None = None
    backend: str | None = None
    git_status_before: str | None = None
    git_diff_before: str | None = None
    checkpoint_path: str = ""
    size_bytes: int = 0
    created_at: str = ""
    restored_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
