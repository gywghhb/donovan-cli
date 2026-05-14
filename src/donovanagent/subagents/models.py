from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SubagentRole(str, Enum):
    RESEARCHER = "researcher"
    CODER = "coder"
    TESTER = "tester"
    REVIEWER = "reviewer"
    SAFETY = "safety"
    BROWSER_QA = "browser_qa"
    PLANNER = "planner"
    CUSTOM = "custom"


@dataclass
class Subagent:
    id: str = ""
    name: str = ""
    role: SubagentRole = SubagentRole.CUSTOM
    goal: str = ""
    prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    permissions: str = "read_only"
    provider: str | None = None
    model: str | None = None
    workspace: str | None = None
    web_search_enabled: bool = True
    execution_backend: str = "local"
    status: str = "pending"  # pending, running, completed, failed
    result_summary: str | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
