from __future__ import annotations

import json
from dataclasses import dataclass, field

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.memory.database import MemoryDatabase
from donovanagent.memory.skills import LearnedSkill, recall_skills
from donovanagent.providers.base import LLMProvider
from donovanagent.utils.json import extract_marked_json_object
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ImprovementContext:
    recalled_skills: list[LearnedSkill] = field(default_factory=list)


class SelfImprovementLoop:
    """Hermes-inspired local learning loop backed by SQLite skills.

    It recalls relevant learned skills before a turn and asks the configured
    model after useful turns whether a durable reusable procedure should be
    created or updated.
    """

    def __init__(self, config: DonovanAgentConfig, db: MemoryDatabase, provider: LLMProvider) -> None:
        self.config = config
        self.db = db
        self.provider = provider

    def before_turn(self, user_input: str) -> ImprovementContext:
        if not self.config.memory.enabled or not self.config.memory.skills_enabled:
            return ImprovementContext()
        # Skip skill recall for short/greeting messages — no meaningful skill context needed
        if len(user_input.split()) < 4:
            return ImprovementContext()
        skills = recall_skills(self.db, user_input, limit=self.config.memory.max_recalled_skills)
        self.db.mark_skills_used([skill.id for skill in skills])
        return ImprovementContext(recalled_skills=skills)

    def after_turn(
        self,
        *,
        session_id: str,
        user_input: str,
        assistant_output: str,
        tool_names: list[str],
        context: ImprovementContext,
    ) -> int | None:
        if not self.config.memory.enabled or not self.config.memory.skills_enabled:
            return None
        if not self._turn_is_worth_learning(user_input, assistant_output, tool_names):
            return None
        prompt = self._analysis_prompt(user_input, assistant_output, tool_names, context)
        try:
            response = self.provider.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You maintain DonovanAgent's reusable skill memory. "
                            "Return only JSON. Do not include secrets, raw tool outputs, "
                            "private file contents, or markdown."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                tools=None,
                tool_choice=None,
            )
        except Exception as exc:
            logger.info("Self-improvement analysis skipped: %s", exc)
            self.db.add_skill_event("analysis_failed", {"error": str(exc), "session_id": session_id})
            return None
        data = extract_marked_json_object(response.content) or _loads_json(response.content)
        if not data or not data.get("should_create"):
            self.db.add_skill_event("analysis_noop", {"session_id": session_id})
            return None
        name = str(data.get("name") or "").strip()
        description = str(data.get("description") or "").strip()
        content = str(data.get("content") or "").strip()
        if not name or not description or not content:
            self.db.add_skill_event("analysis_invalid", {"session_id": session_id, "response": response.content[:500]})
            return None
        skill_id = self.db.upsert_skill(
            name=name,
            description=description,
            content=content,
            source_session_id=session_id,
        )
        self.db.add_audit(
            "self_improvement",
            "agent",
            session_id=session_id,
            approved=True,
            details={"skill_id": skill_id, "name": name},
        )
        return skill_id

    def _turn_is_worth_learning(self, user_input: str, assistant_output: str, tool_names: list[str]) -> bool:
        if len(user_input.split()) >= 8 and tool_names:
            return True
        if len(set(tool_names)) >= 2:
            return True
        lower = user_input.lower()
        return any(word in lower for word in ("remember", "always", "next time", "workflow", "preference"))

    def _analysis_prompt(
        self,
        user_input: str,
        assistant_output: str,
        tool_names: list[str],
        context: ImprovementContext,
    ) -> str:
        existing = "\n".join(
            f"- {skill.name}: {skill.description}\n{skill.content[:600]}"
            for skill in context.recalled_skills
        )
        return (
            "Analyze this completed DonovanAgent turn and decide whether it should become "
            "a durable reusable skill for future work.\n\n"
            "Create or update a skill only if the turn contains a repeatable workflow, "
            "user preference, project convention, troubleshooting pattern, or tool-use "
            "procedure likely to help future sessions.\n\n"
            "Return this exact JSON shape:\n"
            '{"should_create": false, "name": "", "description": "", "content": ""}\n\n'
            "If creating a skill, content must be concise plain text instructions.\n\n"
            f"Existing recalled skills:\n{existing or 'None'}\n\n"
            f"User request:\n{user_input[:4000]}\n\n"
            f"Tools used:\n{', '.join(tool_names) or 'none'}\n\n"
            f"Assistant answer:\n{assistant_output[:4000]}"
        )


def _loads_json(text: str) -> dict[str, object] | None:
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
