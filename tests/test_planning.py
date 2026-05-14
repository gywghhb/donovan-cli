from __future__ import annotations

from typing import Any

from donovanagent.planning.models import PlanItem, Plan, PlanItemStatus
from donovanagent.planning.manager import PlanManager


def _make_config() -> Any:
    from donovanagent.config.schema import DonovanAgentConfig
    return DonovanAgentConfig()


def test_plan_item_status() -> None:
    assert PlanItemStatus.PENDING == "pending"
    assert PlanItemStatus.ACTIVE == "active"
    assert PlanItemStatus.COMPLETED == "completed"
    assert PlanItemStatus.FAILED == "failed"
    assert PlanItemStatus.SKIPPED == "skipped"
    assert PlanItemStatus.BLOCKED == "blocked"


def test_plan_item_defaults() -> None:
    item = PlanItem(title="Test item")
    assert item.status == PlanItemStatus.PENDING
    assert item.item_order == 0
    assert item.id == ""
    assert item.result_summary is None


def test_plan_defaults() -> None:
    plan = Plan(task="test task", items=[PlanItem(title="step 1"), PlanItem(title="step 2")])
    assert plan.status == "pending"
    assert len(plan.items) == 2
    assert plan.id == ""


def test_plan_manager_create() -> None:
    config = _make_config()
    manager = PlanManager(config)

    plan = manager.create_plan(
        task="test",
        items=[{"title": "step 1"}, {"title": "step 2"}, {"title": "step 3"}],
        session_id="sess-1",
    )
    assert plan is not None
    assert plan.task == "test"
    assert len(plan.items) == 3
    assert all(i.id != "" for i in plan.items)


def test_plan_manager_approve_cancel() -> None:
    config = _make_config()
    manager = PlanManager(config)

    manager.create_plan("test", [{"title": "step 1"}], session_id="sess-1")
    assert manager.current_plan.status == "pending"

    manager.approve()
    assert manager.current_plan.status == "approved"
    assert manager.is_active is True

    manager.cancel()
    assert manager.current_plan.status == "cancelled"


def test_plan_manager_lifecycle() -> None:
    config = _make_config()
    manager = PlanManager(config)

    plan = manager.create_plan("lifecycle", [{"title": "a"}, {"title": "b"}], session_id="sess-1")
    item1 = plan.items[0]
    item2 = plan.items[1]

    manager.start_item(item1.id)
    assert item1.status == PlanItemStatus.ACTIVE

    manager.complete_item(item1.id)
    assert item1.status == PlanItemStatus.COMPLETED

    manager.start_item(item2.id)
    manager.fail_item(item2.id, error="oops")
    assert item2.status == PlanItemStatus.FAILED


def test_plan_add_item() -> None:
    config = _make_config()
    manager = PlanManager(config)

    plan = manager.create_plan("add-test", [{"title": "step 1"}], session_id="sess-1")
    manager.add_item(title="step 2 (added later)")

    assert len(manager.current_plan.items) == 2
    assert manager.current_plan.items[-1].title == "step 2 (added later)"


def test_plan_complete() -> None:
    config = _make_config()
    manager = PlanManager(config)

    plan = manager.create_plan("complete-test", [{"title": "only step"}], session_id="sess-1")
    item = plan.items[0]
    manager.start_item(item.id)
    manager.complete_item(item.id)

    manager.complete_plan(summary="done")
    assert manager.current_plan.status == "completed"


def test_plan_manager_without_plan() -> None:
    config = _make_config()
    manager = PlanManager(config)
    assert manager.current_plan is None
    assert manager.is_active is False
    assert manager.get_active_item() is None


def test_plan_auto_advance() -> None:
    config = _make_config()
    manager = PlanManager(config)

    plan = manager.create_plan("auto", [{"title": "first"}, {"title": "second"}], session_id="sess-1")
    item1 = plan.items[0]

    manager.start_item(item1.id)
    manager.complete_item(item1.id)

    # After completing first item, second should auto-activate
    second = plan.items[1]
    assert second.status == PlanItemStatus.ACTIVE


def test_plan_summary() -> None:
    config = _make_config()
    manager = PlanManager(config)

    assert "No active plan" in manager.summary()

    manager.create_plan("summary", [{"title": "step 1"}], session_id="sess-1")
    assert "Plan:" in manager.summary()
    assert "0/1" in manager.summary()


def test_to_checklist_dicts() -> None:
    config = _make_config()
    manager = PlanManager(config)
    assert manager.to_checklist_dicts() == []

    plan = manager.create_plan("checklist", [{"title": "check me"}], session_id="sess-1")
    items = manager.to_checklist_dicts()
    assert len(items) == 1
    assert items[0]["title"] == "check me"
    assert items[0]["status"] == "pending"
