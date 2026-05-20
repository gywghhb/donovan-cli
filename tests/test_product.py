from __future__ import annotations

from pathlib import Path

from donovanagent.product import ProductManager


def test_product_timeline_and_recipe(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = ProductManager(tmp_path / "data", str(workspace))

    manager.record_timeline("turn_started", "hello", session_id="s1")
    assert "hello" in manager.timeline(session_id="s1").body

    manager.create_recipe("Fix Tests", "run pytest and fix failures")
    assert manager.get_recipe_prompt("fix-tests") == "run pytest and fix failures"
    assert "fix-tests" in manager.list_recipes().body


def test_product_graph_and_impact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text(
        "import os\n\nclass Runner:\n    pass\n\ndef run():\n    return os.getcwd()\n",
        encoding="utf-8",
    )
    manager = ProductManager(tmp_path / "data", str(workspace))

    assert "Indexed 1" in manager.build_graph().body
    assert "Runner" in manager.graph_query("runner").body
    assert "pytest" in manager.impact("run", ["pytest"]).body


def test_product_inbox_marketplace_and_profiles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = ProductManager(tmp_path / "data", str(workspace))

    manager.inbox("add clean up docs")
    result = manager.inbox("run")
    assert result.prompt == "clean up docs"

    skill_dir = workspace / ".DonovanAgent" / "skills"
    installed = manager.marketplace("install python-debugger", skill_dir)
    assert "python-debugger" in installed.body
    assert (skill_dir / "python-debugger.md").exists()

    created = manager.profile("create locked no shell")
    assert "locked" in created.body
    active = manager.profile("use locked")
    assert active.body == "locked"


def test_product_watch_check_sandbox_run_and_auto_config(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello", encoding="utf-8")
    manager = ProductManager(tmp_path / "data", str(workspace))

    manager.watch("add README.md")
    assert "ok" in manager.watch("check").body

    sandbox = manager.sandbox_run("touch a file")
    assert "sandbox" in sandbox.body.lower()
    assert "touch a file" in (sandbox.prompt or "")

    router = manager.auto_configure("configure router auto")
    assert router is not None
    assert "auto" in router.body

    recipe = manager.auto_configure("create recipe named docs to update the README")
    assert recipe is not None
    assert manager.get_recipe_prompt("docs") == "update the README"
