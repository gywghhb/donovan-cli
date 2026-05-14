from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SkillType(str, Enum):
    USER = "user"
    LEARNED = "learned"
    SYSTEM = "system"
    PROJECT = "project"
    DRAFT = "draft"


@dataclass
class Skill:
    id: int | str = 0
    name: str = ""
    title: str = ""
    description: str = ""
    content: str = ""
    skill_type: SkillType = SkillType.LEARNED
    triggers: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    workflow_steps: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)
    safety_notes: str = ""
    confidence: float = 1.0
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    source_session_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillDraft:
    id: int | str | None = None
    name: str = ""
    title: str = ""
    description: str = ""
    content: str = ""
    trigger_phrases: list[str] = field(default_factory=list)
    workflow_steps: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)
    safety_notes: str = ""
    confidence: float = 0.0
    reason: str = ""
    source_session_id: str | None = None
    status: str = "draft"
    created_at: str = ""
    updated_at: str = ""


@dataclass
class SkillCandidate:
    """A candidate skill extracted from a turn, before scoring."""
    title: str = ""
    trigger_phrases: list[str] = field(default_factory=list)
    workflow_steps: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)
    safety_notes: str = ""
    confidence: float = 0.0
    source_session: str = ""
    reason: str = ""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def skills_dir(workspace: str, subdir: str = "user") -> str:
    from pathlib import Path
    d = Path(workspace) / ".DonovanAgent" / "skills"
    if subdir:
        d = d / subdir
    return str(d)
