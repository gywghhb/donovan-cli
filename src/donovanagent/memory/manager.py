from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

MEMORY_TYPES = {
    "user_preference", "project_context", "session_summary",
    "workflow", "decision", "error_fix", "command_history",
    "tool_result", "file_summary",
}

SCOPE_VALUES = {"global", "workspace", "session"}


class MemoryManager:
    """Manages persistent memories: preferences, project context, session summaries."""

    def __init__(self, db: MemoryDatabase, config: Any) -> None:
        self.db = db
        self.config = config

    @property
    def enabled(self) -> bool:
        return bool(self.config.memory.enabled)

    def add_memory(
        self,
        memory_type: str,
        title: str,
        content: str,
        scope: str = "workspace",
        workspace_path: str | None = None,
        session_id: str | None = None,
        summary: str | None = None,
        tags: list[str] | None = None,
        confidence: float = 1.0,
        source: str = "agent",
    ) -> int | None:
        """Store a memory in the database."""
        if not self.enabled:
            return None
        if not hasattr(self.db, 'add_memory_record'):
            return None
        try:
            record_id = self.db.add_memory_record(
                memory_type=memory_type, scope=scope,
                workspace_path=workspace_path or self.config.app.default_workspace,
                session_id=session_id, title=title, content=content,
                summary=summary or content[:200],
                tags=tags or [], confidence=confidence, source=source,
            )
            return record_id
        except Exception as exc:
            logger.debug("Failed to add memory: %s", exc)
            return None

    def search(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Search memories."""
        if not self.enabled or not hasattr(self.db, 'search_memories'):
            return []
        if limit is None:
            limit = self.config.memory.max_recall_items
        try:
            return self.db.search_memories(query, limit=limit)
        except Exception as exc:
            logger.debug("Memory search failed: %s", exc)
            return []

    def delete(self, memory_id: int) -> bool:
        """Delete a memory by ID."""
        if not hasattr(self.db, 'delete_memory_record'):
            return False
        try:
            self.db.delete_memory_record(memory_id)
            return True
        except Exception:
            return False

    def summarize_session(self, session_id: str, messages: list[dict[str, Any]]) -> str | None:
        """Create a session summary memory."""
        if not self.enabled or not getattr(self.config.memory, 'auto_summarize_sessions', False):
            return None

        # Build a compact summary from messages
        user_requests = []
        tools_used: set[str] = set()
        files_changed: list[str] = []
        assistant_responses = []

        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))[:200]
            if role == "user":
                user_requests.append(content)
            elif role == "assistant" and content and content != "[tool call]":
                assistant_responses.append(content)
            elif role == "tool":
                meta = msg.get("metadata", {})
                name = meta.get("tool_name", "")
                tools_used.add(name)
                if name in ("write_file", "patch_file"):
                    args = meta.get("arguments", {})
                    if args.get("path"):
                        files_changed.append(str(args["path"]))

        summary_parts = [
            f"Session {session_id[:8]}",
        ]
        if user_requests:
            summary_parts.append(f"Requests: {'; '.join(user_requests[:3])}")
        if tools_used:
            summary_parts.append(f"Tools: {', '.join(sorted(tools_used))}")
        if files_changed:
            summary_parts.append(f"Files: {', '.join(files_changed[:5])}")
        if assistant_responses:
            last = assistant_responses[-1][:150]
            summary_parts.append(f"Result: {last}")

        summary = " | ".join(summary_parts)
        self.add_memory(
            memory_type="session_summary",
            title=f"Session {session_id[:8]}",
            content=summary,
            scope="workspace",
            session_id=session_id,
            summary=summary,
            source="agent_summary",
        )
        return summary

    def generate_project_context(
        self, workspace: str, files: list[str] | None = None
    ) -> dict[str, Any] | None:
        """Generate or update project context for a workspace."""
        if not self.enabled or not getattr(self.config.memory, 'project_context_enabled', False):
            return None
        if not hasattr(self.db, 'upsert_project_context'):
            return None

        import os
        context: dict[str, Any] = {
            "project_type": None,
            "language": None,
            "framework": None,
            "package_manager": None,
            "run_commands": [],
            "test_commands": [],
            "build_commands": [],
        }

        # Detect from common files
        ws = os.path.abspath(workspace)
        if os.path.exists(os.path.join(ws, "package.json")):
            context["language"] = "JavaScript/Node.js"
            context["package_manager"] = "npm"
            context["run_commands"] = ["npm start", "npm run dev"]
            context["test_commands"] = ["npm test"]
            context["build_commands"] = ["npm run build"]
        elif os.path.exists(os.path.join(ws, "pyproject.toml")):
            context["language"] = "Python"
            context["package_manager"] = "pip/uv"
            context["test_commands"] = ["python -m pytest"]
            context["run_commands"] = ["python -m ..."]
        elif os.path.exists(os.path.join(ws, "Cargo.toml")):
            context["language"] = "Rust"
            context["package_manager"] = "cargo"
            context["test_commands"] = ["cargo test"]
            context["build_commands"] = ["cargo build"]
        elif os.path.exists(os.path.join(ws, "go.mod")):
            context["language"] = "Go"
            context["package_manager"] = "go mod"
            context["test_commands"] = ["go test ./..."]
            context["build_commands"] = ["go build ./..."]

        try:
            self.db.upsert_project_context(workspace_path=ws, **context)
        except Exception as exc:
            logger.debug("Failed to save project context: %s", exc)

        return context
