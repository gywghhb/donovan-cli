from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

_SCHEMA_VERSION = 3

MIGRATIONS: dict[int, list[str]] = {
    2: [
        # agent_activity_events
        """CREATE TABLE IF NOT EXISTS agent_activity_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT,
          turn_id TEXT,
          task_id TEXT,
          event_type TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          message TEXT,
          tool_name TEXT,
          tool_args_json TEXT,
          tool_result_summary TEXT,
          elapsed_ms INTEGER,
          status TEXT,
          model TEXT,
          provider TEXT,
          backend TEXT,
          subagent_id TEXT,
          checklist_item_id TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # memories
        """CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          memory_type TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT 'workspace',
          workspace_path TEXT,
          session_id TEXT,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          summary TEXT,
          tags_json TEXT NOT NULL DEFAULT '[]',
          confidence REAL DEFAULT 1.0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_used_at TEXT,
          usage_count INTEGER NOT NULL DEFAULT 0,
          source TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # skill_drafts
        """CREATE TABLE IF NOT EXISTS skill_drafts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '',
          description TEXT,
          content TEXT NOT NULL,
          trigger_phrases_json TEXT NOT NULL DEFAULT '[]',
          workflow_steps_json TEXT NOT NULL DEFAULT '[]',
          required_tools_json TEXT NOT NULL DEFAULT '[]',
          verification_steps_json TEXT NOT NULL DEFAULT '[]',
          safety_notes TEXT,
          confidence REAL DEFAULT 0.0,
          reason TEXT,
          source_session_id TEXT,
          status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # skill_usage
        """CREATE TABLE IF NOT EXISTS skill_usage (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          skill_id INTEGER NOT NULL,
          session_id TEXT,
          turn_id TEXT,
          success INTEGER NOT NULL DEFAULT 1,
          duration_ms INTEGER,
          feedback TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(skill_id) REFERENCES learned_skills(id) ON DELETE CASCADE
        )""",
        # scheduled_tasks
        """CREATE TABLE IF NOT EXISTS scheduled_tasks (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          prompt TEXT NOT NULL,
          schedule_type TEXT NOT NULL,
          cron_expression TEXT,
          interval_seconds INTEGER,
          run_at TEXT,
          timezone TEXT NOT NULL DEFAULT 'UTC',
          workspace_path TEXT,
          provider TEXT,
          model TEXT,
          execution_backend TEXT NOT NULL DEFAULT 'local',
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          last_run_at TEXT,
          next_run_at TEXT,
          last_status TEXT,
          last_result_summary TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # scheduled_task_runs
        """CREATE TABLE IF NOT EXISTS scheduled_task_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          status TEXT,
          result_summary TEXT,
          session_id TEXT,
          error TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
        )""",
        # plans
        """CREATE TABLE IF NOT EXISTS plans (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          turn_id TEXT,
          task TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          approved_at TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # plan_items
        """CREATE TABLE IF NOT EXISTS plan_items (
          id TEXT PRIMARY KEY,
          plan_id TEXT NOT NULL,
          title TEXT NOT NULL,
          description TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          item_order INTEGER NOT NULL DEFAULT 0,
          parent_item_id TEXT,
          started_at TEXT,
          completed_at TEXT,
          result_summary TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
        )""",
        # checkpoints
        """CREATE TABLE IF NOT EXISTS checkpoints (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          turn_id TEXT,
          task_id TEXT,
          reason TEXT,
          tool_name TEXT,
          affected_paths_json TEXT NOT NULL DEFAULT '[]',
          workspace_path TEXT,
          provider TEXT,
          model TEXT,
          backend TEXT,
          git_status_before TEXT,
          git_diff_before TEXT,
          checkpoint_path TEXT NOT NULL,
          size_bytes INTEGER DEFAULT 0,
          created_at TEXT NOT NULL,
          restored_at TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # subagents
        """CREATE TABLE IF NOT EXISTS subagents (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          parent_session_id TEXT,
          name TEXT NOT NULL,
          role TEXT NOT NULL,
          prompt TEXT NOT NULL,
          allowed_tools_json TEXT NOT NULL DEFAULT '[]',
          provider TEXT,
          model TEXT,
          workspace TEXT,
          execution_backend TEXT NOT NULL DEFAULT 'local',
          status TEXT NOT NULL DEFAULT 'pending',
          result_summary TEXT,
          error TEXT,
          started_at TEXT,
          completed_at TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # browser_sessions
        """CREATE TABLE IF NOT EXISTS browser_sessions (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          browser_type TEXT NOT NULL,
          url TEXT,
          status TEXT NOT NULL DEFAULT 'closed',
          started_at TEXT,
          closed_at TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # project_context
        """CREATE TABLE IF NOT EXISTS project_context (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          workspace_path TEXT NOT NULL UNIQUE,
          project_type TEXT,
          language TEXT,
          framework TEXT,
          package_manager TEXT,
          run_commands_json TEXT DEFAULT '[]',
          test_commands_json TEXT DEFAULT '[]',
          build_commands_json TEXT DEFAULT '[]',
          important_folders_json TEXT DEFAULT '[]',
          known_issues_json TEXT DEFAULT '[]',
          learned_workflows_json TEXT DEFAULT '[]',
          last_successful_commands_json TEXT DEFAULT '[]',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )""",
        # Indexes
        "CREATE INDEX IF NOT EXISTS idx_activity_session ON agent_activity_events(session_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_activity_type ON agent_activity_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)",
        "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope, workspace_path)",
        "CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled ON scheduled_tasks(enabled, next_run_at)",
        "CREATE INDEX IF NOT EXISTS idx_plan_items_plan ON plan_items(plan_id, item_order)",
        "CREATE INDEX IF NOT EXISTS idx_checkpoints_session ON checkpoints(session_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_subagents_session ON subagents(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_browser_sessions ON browser_sessions(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_project_context_workspace ON project_context(workspace_path)",
        # schema version
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)",
    ],
    3: [
        # conversation_compacts
        """CREATE TABLE IF NOT EXISTS conversation_compacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          start_msg_id INTEGER,
          end_msg_id INTEGER,
          summary TEXT NOT NULL,
          tool_events_json TEXT NOT NULL DEFAULT '[]',
          compacted_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_compacts_session ON conversation_compacts(session_id, created_at)",
    ],
}


def get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] else 0
    except sqlite3.OperationalError:
        return 0


@contextmanager
def backup_database(db_path: Path) -> Iterator[None]:
    if db_path.exists():
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"DonovanAgent_v{get_schema_version.__code__.co_argcount}_{stamp}.db"
        shutil.copy2(str(db_path), str(backup_path))
        logger.info("Database backed up to %s", backup_path)
    yield


def apply_migrations(db_path: Path) -> None:
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with backup_database(db_path):
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        try:
            current_version = get_schema_version(conn)
            logger.info("Current schema version: %d, target: %d", current_version, _SCHEMA_VERSION)

            for version in range(current_version + 1, _SCHEMA_VERSION + 1):
                statements = MIGRATIONS.get(version)
                if not statements:
                    continue
                logger.info("Applying migration v%d with %d statements", version, len(statements))
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

            # Add FTS for messages if not already done
            _try_fts(conn)
            conn.commit()
        finally:
            conn.close()


def _try_fts(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
            "USING fts5(content, title, summary, tags_json, memory_type UNINDEXED)"
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memories_fts(rowid, content, title, summary, tags_json, memory_type)
              VALUES (new.id, new.content, new.title, new.summary, new.tags_json, new.memory_type);
            END;
            """
        )
    except sqlite3.OperationalError as exc:
        logger.info("FTS5 unavailable for memories: %s", exc)

    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS skill_drafts_fts "
            "USING fts5(name, title, description, content, trigger_phrases_json)"
        )
    except sqlite3.OperationalError as exc:
        logger.info("FTS5 unavailable for skill_drafts: %s", exc)
