from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class PlanItemStatus:
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CHANGED = "changed"
    BLOCKED = "blocked"


@dataclass
class PlanItem:
    id: str = ""
    plan_id: str = ""
    title: str = ""
    description: str = ""
    status: str = PlanItemStatus.PENDING
    item_order: int = 0
    parent_item_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result_summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    id: str = ""
    session_id: str | None = None
    turn_id: str | None = None
    task: str = ""
    status: str = "pending"  # pending, approved, executing, completed, cancelled
    created_at: str = ""
    updated_at: str = ""
    approved_at: str | None = None
    items: list[PlanItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
