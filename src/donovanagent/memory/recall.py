from __future__ import annotations

from donovanagent.memory.database import MemoryDatabase


def recall_relevant(db: MemoryDatabase, text: str, limit: int = 4) -> list[str]:
    words = [word for word in text.split() if len(word) > 4]
    if not words:
        return []
    query = " OR ".join(words[:6])
    rows = db.search_messages(query, limit=limit)
    return [f"{row['role']}: {row['content'][:500]}" for row in rows]
