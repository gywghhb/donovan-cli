from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from donovanagent.db.schema import apply_migrations
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._batch_conn: sqlite3.Connection | None = None
        self._batch_lock = threading.Lock()
        self._batch_depth = 0
        self._audit_buffer: list[dict[str, Any]] = []
        self._message_buffer: list[dict[str, Any]] = []
        self._message_counter = 0
        self._tool_call_buffer: list[dict[str, Any]] = []

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Get a connection. Reuses the batch connection if one is active."""
        if self._batch_conn is not None:
            yield self._batch_conn
            return
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def open_batch(self) -> None:
        """Open a persistent connection for batching multiple operations.
        Must be paired with close_batch(). Supports nesting.
        Safe to call even if a previous batch was not properly closed."""
        with self._batch_lock:
            if self._batch_conn is not None and self._batch_depth == 0:
                try:
                    self._batch_conn.close()
                except Exception:
                    pass
                self._batch_conn = None
            if self._batch_depth == 0:
                self._batch_conn = sqlite3.connect(str(self.path), timeout=10)
                self._batch_conn.row_factory = sqlite3.Row
                self._batch_conn.execute("PRAGMA foreign_keys=ON")
            self._batch_depth += 1

    def close_batch(self) -> None:
        """Flush buffers, commit, and close the persistent batch connection."""
        with self._batch_lock:
            self._batch_depth -= 1
            if self._batch_depth <= 0:
                self._batch_depth = 0
                if self._batch_conn is not None:
                    try:
                        self._flush_audit_buffer()
                        self._flush_tool_call_buffer()
                        self._flush_message_buffer()
                        self._batch_conn.commit()
                    finally:
                        self._batch_conn.close()
                        self._batch_conn = None

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Context manager that batches DB operations into a single connection."""
        self.open_batch()
        try:
            yield
        finally:
            self.close_batch()

    def initialize(self) -> None:
        """Initialize the database with schema and migrations."""
        # Apply versioned migrations
        apply_migrations(self.path)

        # Legacy schema initialization (idempotent)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(schema)
            self._try_enable_fts(conn)

    def _try_enable_fts(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                "USING fts5(content, session_id UNINDEXED, role UNINDEXED)"
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                  INSERT INTO messages_fts(rowid, content, session_id, role)
                  VALUES (new.id, new.content, new.session_id, new.role);
                END;
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                  INSERT INTO messages_fts(messages_fts, rowid, content, session_id, role)
                  VALUES('delete', old.id, old.content, old.session_id, old.role);
                END;
                """
            )
        except sqlite3.OperationalError as exc:
            logger.info("SQLite FTS5 unavailable: %s", exc)

    def fts_available(self) -> bool:
        with self.connect() as conn:
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_probe USING fts5(x)")
                conn.execute("DROP TABLE IF EXISTS fts_probe")
                return True
            except sqlite3.OperationalError:
                return False

    # â”€â”€ Session methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def create_session(self, workspace: str, provider: str, model: str, title: str = "New chat") -> str:
        session_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, title, created_at, updated_at, workspace, provider, model)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, title, now, now, workspace, provider, model),
            )
        return session_id

    def update_session_title(self, session_id: str, title: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title[:80], utc_now(), session_id),
            )

    def touch_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (utc_now(), session_id))

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        token_count: int | None = None,
    ) -> int:
        """Add a message. All roles are buffered when batch connection is active."""
        now = utc_now()
        record: dict[str, Any] = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": now,
            "token_count": token_count,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False),
        }
        with self._batch_lock:
            if self._batch_conn is not None:
                self._message_counter += 1
                self._message_buffer.append(record)
                return -self._message_counter  # negative placeholder, remapped on flush
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages(session_id, role, content, created_at, token_count, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    now,
                    token_count,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            return int(cur.lastrowid)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at, workspace, provider, model
                FROM sessions ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_messages(self, session_id: str, limit: int = 24) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at, metadata_json
                FROM messages WHERE session_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_messages_before(self, session_id: str, before_id: int) -> list[dict[str, Any]]:
        """Return all messages for a session with id < before_id, in ascending order."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, created_at, metadata_json
                FROM messages WHERE session_id = ? AND id < ?
                ORDER BY id ASC
                """,
                (session_id, before_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_conversation_compact(
        self,
        session_id: str,
        start_msg_id: int,
        end_msg_id: int,
        summary: str,
        tool_events_json: str = "[]",
        compacted_count: int = 0,
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO conversation_compacts(session_id, start_msg_id, end_msg_id, summary, tool_events_json, compacted_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, start_msg_id, end_msg_id, summary, tool_events_json, compacted_count, now),
            )
            return int(cur.lastrowid)

    def get_conversation_compacts(self, session_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, start_msg_id, end_msg_id, summary, tool_events_json, compacted_count, created_at
                FROM conversation_compacts
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_message_metadata(self, message_id: int, metadata: dict[str, Any]) -> None:
        existing = {}
        with self.connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row:
                existing = json.loads(row["metadata_json"])
            existing.update(metadata)
            conn.execute(
                "UPDATE messages SET metadata_json = ? WHERE id = ?",
                (json.dumps(existing, ensure_ascii=False), message_id),
            )

    def search_messages(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT m.session_id, m.role, m.content, m.created_at
                    FROM messages_fts f
                    JOIN messages m ON m.id = f.rowid
                    WHERE messages_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT session_id, role, content, created_at
                    FROM messages
                    WHERE content LIKE ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
        return [dict(row) for row in rows]

    # â”€â”€ Tool call methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        *,
        message_id: int | None = None,
        tool_call_id: str | None = None,
        exit_code: int | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        approved: bool | None = None,
        approval_reason: str | None = None,
    ) -> int:
        """Record a tool call. Buffered when batch connection is active."""
        record: dict[str, Any] = {
            "session_id": session_id,
            "message_id": message_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments_json": json.dumps(arguments, ensure_ascii=False),
            "result_json": json.dumps(result, ensure_ascii=False),
            "exit_code": exit_code,
            "started_at": started_at or utc_now(),
            "finished_at": finished_at or utc_now(),
            "approved": None if approved is None else int(approved),
            "approval_reason": approval_reason,
        }
        with self._batch_lock:
            if self._batch_conn is not None:
                self._tool_call_buffer.append(record)
                return len(self._tool_call_buffer)
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tool_calls(
                  session_id, message_id, tool_call_id, tool_name, arguments_json, result_json,
                  exit_code, started_at, finished_at, approved, approval_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    message_id,
                    tool_call_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    exit_code,
                    started_at or utc_now(),
                    finished_at or utc_now(),
                    None if approved is None else int(approved),
                    approval_reason,
                ),
            )
            return int(cur.lastrowid)

    # â”€â”€ Audit methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_audit(
        self,
        action_type: str,
        actor: str,
        *,
        session_id: str | None = None,
        path: str | None = None,
        command: str | None = None,
        risk_level: str | None = None,
        approved: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        """Add an audit log entry. Buffered when batch connection is active."""
        record: dict[str, Any] = {
            "session_id": session_id,
            "action_type": action_type,
            "actor": actor,
            "path": path,
            "command": command,
            "risk_level": risk_level,
            "approved": None if approved is None else int(approved),
            "details_json": json.dumps(details or {}, ensure_ascii=False),
            "created_at": utc_now(),
        }
        with self._batch_lock:
            if self._batch_conn is not None:
                self._audit_buffer.append(record)
                return len(self._audit_buffer)
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO audit_log(
                  session_id, action_type, actor, path, command, risk_level, approved,
                  details_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    action_type,
                    actor,
                    path,
                    command,
                    risk_level,
                    None if approved is None else int(approved),
                    json.dumps(details or {}, ensure_ascii=False),
                    utc_now(),
                ),
            )
            return int(cur.lastrowid)

    def add_config_event(self, event_type: str, details: dict[str, Any] | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO config_events(event_type, details_json, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(details or {}, ensure_ascii=False), utc_now()),
            )
            return int(cur.lastrowid)

    # â”€â”€ Learned skill methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def upsert_skill(
        self,
        name: str,
        description: str,
        content: str,
        *,
        source_session_id: str | None = None,
    ) -> int:
        now = utc_now()
        normalized = normalize_skill_name(name)
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM learned_skills WHERE lower(name) = lower(?)",
                (normalized,),
            ).fetchone()
            if existing:
                skill_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE learned_skills
                    SET description = ?, content = ?, source_session_id = COALESCE(?, source_session_id),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (description, content, source_session_id, now, skill_id),
                )
                conn.execute(
                    "INSERT INTO skill_events(skill_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                    (skill_id, "updated", json.dumps({"name": normalized}), now),
                )
                return skill_id
            cur = conn.execute(
                """
                INSERT INTO learned_skills(
                  name, description, content, source_session_id, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (normalized, description, content, source_session_id, now, now),
            )
            skill_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO skill_events(skill_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                (skill_id, "created", json.dumps({"name": normalized}), now),
            )
            return skill_id

    def list_skills(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, content, created_at, updated_at, uses, last_used_at
                FROM learned_skills
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_skills(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        terms = [term.lower() for term in query.split() if len(term) > 3]
        if not terms:
            return []
        pattern = "%" + "%".join(terms[:4]) + "%"
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, content, uses, updated_at
                FROM learned_skills
                WHERE lower(name || ' ' || description || ' ' || content) LIKE ?
                ORDER BY uses DESC, updated_at DESC
                LIMIT ?
                """,
                (pattern, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_skills_used(self, skill_ids: list[int]) -> None:
        if not skill_ids:
            return
        now = utc_now()
        with self.connect() as conn:
            for skill_id in skill_ids:
                conn.execute(
                    "UPDATE learned_skills SET uses = uses + 1, last_used_at = ? WHERE id = ?",
                    (now, skill_id),
                )
                conn.execute(
                    "INSERT INTO skill_events(skill_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                    (skill_id, "used", "{}", now),
                )

    def add_skill_event(
        self, event_type: str, details: dict[str, Any], skill_id: int | None = None
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO skill_events(skill_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
                (skill_id, event_type, json.dumps(details, ensure_ascii=False), utc_now()),
            )
            return int(cur.lastrowid)

    # â”€â”€ Activity event methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_activity_event(
        self,
        session_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
        event_type: str = "",
        timestamp: str = "",
        message: str = "",
        tool_name: str | None = None,
        tool_args_json: str | None = None,
        tool_result_summary: str | None = None,
        elapsed_ms: int | None = None,
        status: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        backend: str | None = None,
        subagent_id: str | None = None,
        checklist_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = timestamp or utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_activity_events(
                  session_id, turn_id, task_id, event_type, timestamp, message,
                  tool_name, tool_args_json, tool_result_summary, elapsed_ms, status,
                  model, provider, backend, subagent_id, checklist_item_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, turn_id, task_id, event_type, now, message,
                    tool_name, tool_args_json, tool_result_summary, elapsed_ms, status,
                    model, provider, backend, subagent_id, checklist_item_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def batch_add_activity_events(self, events: list[dict[str, Any]]) -> None:
        """Insert multiple activity events in a single transaction."""
        if not events:
            return
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO agent_activity_events(
                  session_id, turn_id, task_id, event_type, timestamp, message,
                  tool_name, tool_args_json, tool_result_summary, elapsed_ms, status,
                  model, provider, backend, subagent_id, checklist_item_id, metadata_json
                ) VALUES (
                  :session_id, :turn_id, :task_id, :event_type,
                  COALESCE(:timestamp, datetime('now')),
                  :message, :tool_name, :tool_args_json, :tool_result_summary, :elapsed_ms,
                  :status, :model, :provider, :backend, :subagent_id, :checklist_item_id,
                  :metadata_json
                )
                """,
                events,
            )

    def _flush_audit_buffer(self) -> None:
        """Flush buffered audit log entries in a single executemany call."""
        if not self._audit_buffer or self._batch_conn is None:
            return
        try:
            self._batch_conn.executemany(
                """
                INSERT INTO audit_log(
                  session_id, action_type, actor, path, command, risk_level, approved,
                  details_json, created_at
                ) VALUES (
                  :session_id, :action_type, :actor, :path, :command, :risk_level,
                  :approved, :details_json, :created_at
                )
                """,
                self._audit_buffer,
            )
        except Exception as exc:
            logger.debug("Failed to flush audit buffer: %s", exc)
        self._audit_buffer = []

    def _flush_tool_call_buffer(self) -> None:
        """Flush buffered tool call records in a single executemany call."""
        if not self._tool_call_buffer or self._batch_conn is None:
            return
        try:
            self._batch_conn.executemany(
                """
                INSERT INTO tool_calls(
                  session_id, message_id, tool_call_id, tool_name,
                  arguments_json, result_json, exit_code,
                  started_at, finished_at, approved, approval_reason
                ) VALUES (
                  :session_id, :message_id, :tool_call_id, :tool_name,
                  :arguments_json, :result_json, :exit_code,
                  :started_at, :finished_at, :approved, :approval_reason
                )
                """,
                self._tool_call_buffer,
            )
        except Exception as exc:
            logger.debug("Failed to flush tool call buffer: %s", exc)
        self._tool_call_buffer = []

    def _flush_message_buffer(self) -> None:
        """Flush buffered messages of all roles in a single executemany call."""
        if not self._message_buffer or self._batch_conn is None:
            return
        try:
            self._batch_conn.executemany(
                """
                INSERT INTO messages(session_id, role, content, created_at, token_count, metadata_json)
                VALUES (
                  :session_id, :role, :content, :created_at, :token_count, :metadata_json
                )
                """,
                self._message_buffer,
            )
            # Touch session once for the batch
            if self._message_buffer:
                self._batch_conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?",
                    (self._message_buffer[-1]["created_at"], self._message_buffer[-1]["session_id"]),
                )
        except Exception as exc:
            logger.debug("Failed to flush message buffer: %s", exc)
        self._message_buffer = []
        self._message_counter = 0

    # â”€â”€ Memory record methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_memory_record(
        self,
        memory_type: str,
        scope: str = "workspace",
        workspace_path: str = "",
        session_id: str | None = None,
        title: str = "",
        content: str = "",
        summary: str = "",
        tags: list[str] | None = None,
        confidence: float = 1.0,
        source: str = "agent",
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO memories(
                  memory_type, scope, workspace_path, session_id, title, content, summary,
                  tags_json, confidence, created_at, updated_at, last_used_at, usage_count, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_type, scope, workspace_path, session_id, title, content, summary,
                    json.dumps(tags or [], ensure_ascii=False),
                    confidence, now, now, now, 1, source,
                ),
            )
            return int(cur.lastrowid)

    def search_memories(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        with self.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT m.* FROM memories_fts f
                    JOIN memories m ON m.id = f.rowid
                    WHERE memories_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE content LIKE ? OR title LIKE ? OR summary LIKE ?
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%", limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def delete_memory_record(self, memory_id: int) -> None:
        with self.connect() as conn:
            try:
                conn.execute("DELETE FROM memories_fts WHERE rowid = ?", (memory_id,))
            except sqlite3.OperationalError:
                pass
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def update_memory_usage(self, memory_id: int) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE memories SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
                (now, memory_id),
            )

    # â”€â”€ Skill draft methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_skill_draft(
        self,
        name: str = "",
        title: str = "",
        description: str = "",
        content: str = "",
        trigger_phrases: list[str] | None = None,
        workflow_steps: list[str] | None = None,
        required_tools: list[str] | None = None,
        verification_steps: list[str] | None = None,
        safety_notes: str = "",
        confidence: float = 0.0,
        reason: str = "",
        source_session_id: str | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO skill_drafts(
                  name, title, description, content, trigger_phrases_json,
                  workflow_steps_json, required_tools_json, verification_steps_json,
                  safety_notes, confidence, reason, source_session_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name, title, description or "", content,
                    json.dumps(trigger_phrases or []),
                    json.dumps(workflow_steps or []),
                    json.dumps(required_tools or []),
                    json.dumps(verification_steps or []),
                    safety_notes, confidence, reason, source_session_id,
                    "draft", now, now,
                ),
            )
            return int(cur.lastrowid)

    # â”€â”€ Skill usage methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_skill_usage(
        self,
        skill_id: int = 0,
        session_id: str = "",
        turn_id: str = "",
        success: bool = True,
        duration_ms: int | None = None,
        feedback: str = "",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO skill_usage(skill_id, session_id, turn_id, success, duration_ms, feedback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (skill_id, session_id, turn_id, int(success), duration_ms, feedback, utc_now()),
            )
            return int(cur.lastrowid)

    # â”€â”€ Scheduled task methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def add_scheduled_task(self, task: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scheduled_tasks(
                  id, name, prompt, schedule_type, cron_expression, interval_seconds,
                  run_at, timezone, workspace_path, provider, model, execution_backend,
                  enabled, created_at, updated_at, last_run_at, next_run_at,
                  last_status, last_result_summary, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id, task.name, task.prompt, task.schedule_type,
                    task.cron_expression, task.interval_seconds, task.run_at,
                    task.timezone, task.workspace_path, task.provider, task.model,
                    task.execution_backend, int(task.enabled),
                    task.created_at, task.updated_at, task.last_run_at, task.next_run_at,
                    task.last_status, task.last_result_summary,
                    json.dumps(task.metadata or {}, ensure_ascii=False),
                ),
            )

    def list_scheduled_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_scheduled_task(self, task_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))

    def update_scheduled_task(self, task: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE scheduled_tasks SET
                  name=?, prompt=?, schedule_type=?, cron_expression=?,
                  interval_seconds=?, run_at=?, timezone=?, workspace_path=?,
                  provider=?, model=?, execution_backend=?, enabled=?,
                  updated_at=?, last_run_at=?, next_run_at=?, last_status=?,
                  last_result_summary=?, metadata_json=?
                WHERE id=?
                """,
                (
                    task.name, task.prompt, task.schedule_type, task.cron_expression,
                    task.interval_seconds, task.run_at, task.timezone, task.workspace_path,
                    task.provider, task.model, task.execution_backend, int(task.enabled),
                    task.updated_at, task.last_run_at, task.next_run_at, task.last_status,
                    task.last_result_summary,
                    json.dumps(task.metadata or {}, ensure_ascii=False),
                    task.id,
                ),
            )

    def add_scheduled_task_run(
        self,
        task_id: str = "",
        status: str = "",
        result_summary: str = "",
        session_id: str | None = None,
        error: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scheduled_task_runs(
                  task_id, started_at, finished_at, status, result_summary, session_id, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, utc_now(), utc_now(), status, result_summary, session_id, error),
            )
            return int(cur.lastrowid)

    # â”€â”€ Plan methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def save_plan(self, plan: Any) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO plans(
                  id, session_id, turn_id, task, status, created_at, updated_at, approved_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.id, plan.session_id, plan.turn_id, plan.task, plan.status,
                    plan.created_at or now, plan.updated_at or now, plan.approved_at,
                    json.dumps(plan.metadata or {}, ensure_ascii=False),
                ),
            )
            for item in plan.items:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO plan_items(
                      id, plan_id, title, description, status, item_order,
                      parent_item_id, started_at, completed_at, result_summary, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.id, plan.id, item.title, item.description, item.status,
                        item.item_order, item.parent_item_id, item.started_at,
                        item.completed_at, item.result_summary,
                        json.dumps(item.metadata or {}, ensure_ascii=False),
                    ),
                )

    # â”€â”€ Subagent methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def save_subagent(self, subagent: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO subagents(
                  id, session_id, name, role, prompt, allowed_tools_json,
                  provider, model, workspace, execution_backend, status,
                  result_summary, error, started_at, completed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subagent.id, subagent.session_id, subagent.name,
                    subagent.role.value if hasattr(subagent.role, 'value') else subagent.role,
                    subagent.prompt, json.dumps(subagent.allowed_tools),
                    subagent.provider, subagent.model, subagent.workspace,
                    subagent.execution_backend, subagent.status,
                    subagent.result_summary, subagent.error,
                    subagent.started_at, subagent.completed_at,
                    json.dumps(subagent.metadata or {}, ensure_ascii=False),
                ),
            )

    # â”€â”€ Project context methods â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def upsert_project_context(self, workspace_path: str, **kwargs: Any) -> int:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM project_context WHERE workspace_path = ?",
                (workspace_path,),
            ).fetchone()
            if existing:
                pid = int(existing["id"])
                fields = ", ".join(f"{k} = ?" for k in kwargs)
                values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in kwargs.values()]
                conn.execute(
                    f"UPDATE project_context SET {fields}, updated_at = ? WHERE id = ?",
                    (*values, now, pid),
                )
                return pid
            fields = ", ".join(kwargs.keys())
            placeholders = ", ".join("?" for _ in kwargs)
            values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for v in kwargs.values()]
            cur = conn.execute(
                f"INSERT INTO project_context(workspace_path, {fields}, created_at, updated_at) VALUES (?, {placeholders}, ?, ?)",
                (workspace_path, *values, now, now),
            )
            return int(cur.lastrowid)

    def get_project_context(self, workspace_path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_context WHERE workspace_path = ?",
                (workspace_path,),
            ).fetchone()
        return dict(row) if row else None


def normalize_skill_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    return cleaned[:80] or "Untitled skill"
