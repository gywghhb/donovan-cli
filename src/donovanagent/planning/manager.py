from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from donovanagent.planning.models import Plan, PlanItem, PlanItemStatus
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class PlanManager:
    """Manages plan creation, approval, execution tracking, and dynamic updates."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.current_plan: Plan | None = None
        self._on_status_change: Callable[[PlanItem], None] | None = None

    def set_status_callback(self, callback: Callable[[PlanItem], None]) -> None:
        self._on_status_change = callback

    @property
    def is_active(self) -> bool:
        return self.current_plan is not None and self.current_plan.status in ("approved", "executing")

    @property
    def requires_approval(self) -> bool:
        cfg = self.config.plan if hasattr(self.config, "plan") else None
        return bool(cfg and cfg.require_approval)

    def create_plan(self, task: str, items: list[dict[str, str]], session_id: str | None = None) -> Plan:
        """Create a new plan from a task description and item list."""
        now = datetime.now(timezone.utc).isoformat()
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        plan_items = []
        for i, item in enumerate(items):
            plan_items.append(PlanItem(
                id=f"{plan_id}_item_{i}",
                plan_id=plan_id,
                title=item.get("title", item.get("description", f"Step {i + 1}")),
                description=item.get("description", ""),
                item_order=i,
                status=PlanItemStatus.PENDING,
            ))

        self.current_plan = Plan(
            id=plan_id,
            session_id=session_id,
            task=task,
            status="pending",
            created_at=now,
            updated_at=now,
            items=plan_items,
        )
        return self.current_plan

    def start_item(self, item_id: str) -> PlanItem | None:
        """Mark a plan item as active."""
        item = self._find_item(item_id)
        if not item:
            return None
        item.status = PlanItemStatus.ACTIVE
        item.started_at = datetime.now(timezone.utc).isoformat()
        self.current_plan.updated_at = item.started_at
        if self._on_status_change:
            self._on_status_change(item)
        return item

    def complete_item(self, item_id: str, summary: str = "") -> PlanItem | None:
        """Mark a plan item as completed."""
        item = self._find_item(item_id)
        if not item:
            return None
        item.status = PlanItemStatus.COMPLETED
        item.completed_at = datetime.now(timezone.utc).isoformat()
        item.result_summary = summary
        self.current_plan.updated_at = item.completed_at
        if self._on_status_change:
            self._on_status_change(item)
        # Auto-advance to next pending item
        self._auto_advance()
        return item

    def fail_item(self, item_id: str, error: str = "") -> PlanItem | None:
        """Mark a plan item as failed."""
        item = self._find_item(item_id)
        if not item:
            return None
        item.status = PlanItemStatus.FAILED
        item.completed_at = datetime.now(timezone.utc).isoformat()
        item.result_summary = error
        self.current_plan.updated_at = item.completed_at
        if self._on_status_change:
            self._on_status_change(item)
        return item

    def skip_item(self, item_id: str, reason: str = "") -> PlanItem | None:
        """Mark a plan item as skipped."""
        item = self._find_item(item_id)
        if not item:
            return None
        item.status = PlanItemStatus.SKIPPED
        item.result_summary = reason
        if self._on_status_change:
            self._on_status_change(item)
        self._auto_advance()
        return item

    def add_item(self, title: str, description: str = "", after_item_id: str | None = None) -> PlanItem:
        """Dynamically add an item to the plan."""
        if not self.current_plan:
            return None
        now = datetime.now(timezone.utc).isoformat()
        new_id = f"{self.current_plan.id}_item_{len(self.current_plan.items)}_dynamic"
        insert_order = 0
        if after_item_id:
            after = self._find_item(after_item_id)
            if after:
                insert_order = after.item_order + 1
                # Shift subsequent items
                for item in self.current_plan.items:
                    if item.item_order >= insert_order:
                        item.item_order += 1
        else:
            insert_order = len(self.current_plan.items)

        item = PlanItem(
            id=new_id,
            plan_id=self.current_plan.id,
            title=title,
            description=description,
            item_order=insert_order,
            status=PlanItemStatus.PENDING,
        )
        self.current_plan.items.append(item)
        self.current_plan.items.sort(key=lambda x: x.item_order)
        self.current_plan.updated_at = now
        return item

    def approve(self) -> None:
        """Mark the plan as approved."""
        if not self.current_plan:
            return
        self.current_plan.status = "approved"
        self.current_plan.approved_at = datetime.now(timezone.utc).isoformat()
        self.current_plan.updated_at = self.current_plan.approved_at

    def start(self) -> None:
        """Start executing the plan."""
        if not self.current_plan:
            return
        self.current_plan.status = "executing"
        self.current_plan.updated_at = datetime.now(timezone.utc).isoformat()
        # Start first pending item
        for item in self.current_plan.items:
            if item.status == PlanItemStatus.PENDING:
                self.start_item(item.id)
                break

    def cancel(self) -> None:
        """Cancel the current plan."""
        if not self.current_plan:
            return
        self.current_plan.status = "cancelled"
        self.current_plan.updated_at = datetime.now(timezone.utc).isoformat()
        for item in self.current_plan.items:
            if item.status == PlanItemStatus.ACTIVE:
                item.status = PlanItemStatus.SKIPPED

    def complete_plan(self, summary: str = "") -> None:
        """Mark all remaining items and the plan as completed."""
        if not self.current_plan:
            return
        for item in self.current_plan.items:
            if item.status in (PlanItemStatus.PENDING, PlanItemStatus.ACTIVE):
                item.status = PlanItemStatus.COMPLETED
                item.completed_at = datetime.now(timezone.utc).isoformat()
        self.current_plan.status = "completed"
        self.current_plan.updated_at = datetime.now(timezone.utc).isoformat()

    def get_active_item(self) -> PlanItem | None:
        """Get the currently active plan item."""
        if not self.current_plan:
            return None
        for item in self.current_plan.items:
            if item.status == PlanItemStatus.ACTIVE:
                return item
        return None

    def get_pending_items(self) -> list[PlanItem]:
        """Get items still pending or active."""
        if not self.current_plan:
            return []
        return [i for i in self.current_plan.items if i.status in (PlanItemStatus.PENDING, PlanItemStatus.ACTIVE)]

    def summary(self) -> str:
        """Get a text summary of the plan progress."""
        if not self.current_plan:
            return "No active plan."
        total = len(self.current_plan.items)
        completed = sum(1 for i in self.current_plan.items if i.status == PlanItemStatus.COMPLETED)
        failed = sum(1 for i in self.current_plan.items if i.status == PlanItemStatus.FAILED)
        active = sum(1 for i in self.current_plan.items if i.status == PlanItemStatus.ACTIVE)
        return f"Plan: {completed}/{total} items completed ({failed} failed, {active} active)"

    def _find_item(self, item_id: str) -> PlanItem | None:
        if not self.current_plan:
            return None
        for item in self.current_plan.items:
            if item.id == item_id:
                return item
        return None

    def _auto_advance(self) -> None:
        """Advance to the next pending item if current is done."""
        if not self.current_plan:
            return
        for item in self.current_plan.items:
            if item.status == PlanItemStatus.PENDING:
                self.start_item(item.id)
                break

    def to_checklist_dicts(self) -> list[dict[str, Any]]:
        """Convert plan items to checklist display dictionaries."""
        if not self.current_plan:
            return []
        return [
            {
                "id": item.id,
                "title": item.title,
                "status": item.status,
                "description": item.description,
            }
            for item in sorted(self.current_plan.items, key=lambda x: x.item_order)
        ]
