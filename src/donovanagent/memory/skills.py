from __future__ import annotations

from dataclasses import dataclass

from donovanagent.memory.database import MemoryDatabase


@dataclass(frozen=True)
class LearnedSkill:
    id: int
    name: str
    description: str
    content: str


def recall_skills(db: MemoryDatabase, query: str, limit: int = 4) -> list[LearnedSkill]:
    rows = db.search_skills(query, limit=limit)
    return [
        LearnedSkill(
            id=int(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            content=str(row["content"]),
        )
        for row in rows
    ]
