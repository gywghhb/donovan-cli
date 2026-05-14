from __future__ import annotations

from typing import Any

from donovanagent.memory.database import MemoryDatabase


class AuditLog:
    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db

    def record(self, action_type: str, actor: str, **kwargs: Any) -> int:
        return self.db.add_audit(action_type, actor, **kwargs)
