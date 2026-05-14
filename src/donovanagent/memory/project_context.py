from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


def detect_project_context(workspace: str) -> dict[str, Any]:
    """Auto-detect project type and configuration from workspace files."""
    ws = Path(workspace).expanduser().resolve()
    context: dict[str, Any] = {
        "project_type": None,
        "language": None,
        "framework": None,
        "package_manager": None,
        "run_commands": [],
        "test_commands": [],
        "build_commands": [],
        "important_folders": [],
        "known_issues": [],
    }

    # Python
    if (ws / "pyproject.toml").exists():
        context["language"] = "Python"
        context["package_manager"] = "pip/uv"
        context["test_commands"] = ["python -m pytest"]
        context["project_type"] = "Python package"
        if (ws / "setup.py").exists() or (ws / "setup.cfg").exists():
            context["run_commands"] = ["python -m pip install -e ."]

    # Node.js
    if (ws / "package.json").exists():
        context["language"] = "JavaScript/Node.js"
        context["package_manager"] = "npm"
        context["run_commands"] = ["npm start", "npm run dev"]
        context["test_commands"] = ["npm test"]
        context["build_commands"] = ["npm run build"]
        try:
            import json
            pkg = json.loads((ws / "package.json").read_text(encoding="utf-8"))
            if "vite" in str(pkg.get("devDependencies", {})):
                context["framework"] = "Vite"
            elif "next" in str(pkg.get("dependencies", {})):
                context["framework"] = "Next.js"
            elif "react" in str(pkg.get("dependencies", {})):
                context["framework"] = "React"
            if "pnpm" in pkg.get("packageManager", ""):
                context["package_manager"] = "pnpm"
            elif "yarn" in pkg.get("packageManager", ""):
                context["package_manager"] = "yarn"
        except Exception:
            pass

    # Rust
    if (ws / "Cargo.toml").exists():
        context["language"] = "Rust"
        context["package_manager"] = "cargo"
        context["test_commands"] = ["cargo test"]
        context["build_commands"] = ["cargo build"]

    # Go
    if (ws / "go.mod").exists():
        context["language"] = "Go"
        context["package_manager"] = "go mod"
        context["test_commands"] = ["go test ./..."]
        context["build_commands"] = ["go build ./..."]

    # Detect important folders
    important_dirs = ["src", "lib", "app", "components", "tests", "docs", "public", "assets"]
    for d in important_dirs:
        if (ws / d).is_dir():
            context["important_folders"].append(d)

    return context


def save_project_context(db: MemoryDatabase, workspace: str) -> dict[str, Any]:
    """Detect and save project context to database."""
    context = detect_project_context(workspace)
    if hasattr(db, 'upsert_project_context'):
        try:
            db.upsert_project_context(workspace_path=str(Path(workspace).resolve()), **context)
        except Exception as exc:
            logger.debug("Failed to save project context: %s", exc)
    return context
