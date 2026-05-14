from __future__ import annotations

from donovanagent.memory.database import MemoryDatabase


class SessionStore:
    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db

    def start(self, workspace: str, provider: str, model: str) -> str:
        return self.db.create_session(workspace=workspace, provider=provider, model=model)

    def list(self, limit: int = 50) -> list[dict[str, object]]:
        return self.db.list_sessions(limit=limit)
