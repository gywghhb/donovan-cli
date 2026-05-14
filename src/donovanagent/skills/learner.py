from __future__ import annotations

import json
from typing import Any

from donovanagent.skills.models import SkillCandidate, SkillDraft
from donovanagent.skills.manager import SkillManager
from donovanagent.skills.ranker import SkillRanker
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class SkillLearner:
    """Analyzes agent turns and extracts reusable skills."""

    def __init__(self, skill_manager: SkillManager, ranker: SkillRanker, config: Any) -> None:
        self.manager = skill_manager
        self.ranker = ranker
        self.config = config

    def should_learn(self, user_input: str, tool_names: list[str], result_success: bool) -> bool:
        """Determine if a turn is worth extracting a skill from."""
        cfg = self.config.skills if hasattr(self.config, "skills") else None
        if cfg and not cfg.auto_learn:
            return False
        if not result_success:
            return False
        if len(set(tool_names)) >= 3:
            return True
        if len(user_input.split()) >= 8 and tool_names:
            return True
        keywords = ("remember", "always", "next time", "from now on", "workflow", "preference")
        return any(word in user_input.lower() for word in keywords)

    def extract_candidate(
        self,
        user_input: str,
        assistant_output: str,
        tool_names: list[str],
        tool_results: list[str],
    ) -> SkillCandidate | None:
        """Create a structured candidate from a turn. Called when the model proposes a skill."""
        if not self.should_learn(user_input, tool_names, True):
            return None
        return SkillCandidate(
            title=_title_from_input(user_input),
            trigger_phrases=_triggers_from_input(user_input, tool_names),
            workflow_steps=_steps_from_tools(tool_names),
            required_tools=list(set(tool_names)),
            verification_steps=[],
            safety_notes="",
            confidence=0.5,  # Default medium Ã¢â‚¬â€ will be scored
            source_session="",
            reason=f"Extracted from multi-tool turn ({len(set(tool_names))} tools)",
        )

    def process_candidate(self, candidate: SkillCandidate) -> SkillDraft | None:
        """Score and save a candidate as a skill or draft."""
        if not candidate.title or not candidate.workflow_steps:
            return None

        score = self.ranker.score_candidate(candidate)
        candidate.confidence = score

        draft = SkillDraft(
            name=_name_from_title(candidate.title),
            title=candidate.title,
            description=candidate.reason,
            content=_draft_content(candidate),
            trigger_phrases=candidate.trigger_phrases,
            workflow_steps=candidate.workflow_steps,
            required_tools=candidate.required_tools,
            verification_steps=candidate.verification_steps,
            safety_notes=candidate.safety_notes,
            confidence=score,
            reason=candidate.reason,
            source_session_id=candidate.source_session,
        )

        cfg = self.config.skills if hasattr(self.config, "skills") else None
        auto_save_threshold = cfg.auto_save_confidence_threshold if cfg else 0.8
        draft_threshold = cfg.draft_confidence_threshold if cfg else 0.5

        if score >= auto_save_threshold:
            # Save as learned skill immediately
            from donovanagent.skills.models import Skill, SkillType, now_iso
            skill = Skill(
                name=draft.name,
                title=draft.title,
                description=draft.description,
                content=draft.content,
                skill_type=SkillType.LEARNED,
                triggers=draft.trigger_phrases,
                tools=draft.required_tools,
                workflow_steps=draft.workflow_steps,
                verification_steps=draft.verification_steps,
                safety_notes=draft.safety_notes,
                confidence=score,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            self.manager.save_file(skill)
            logger.info("Auto-saved learned skill: %s (confidence: %.2f)", skill.name, score)
            draft._auto_saved = True  # type: ignore[attr-defined]
            return draft
        elif score >= draft_threshold:
            # Save as draft
            self.manager.save_draft(draft)
            logger.info("Saved draft skill: %s (confidence: %.2f)", draft.name, score)
            return draft

        logger.info("Skill candidate %s below threshold (%.2f), discarded", draft.name, score)
        return None


def _title_from_input(text: str) -> str:
    words = text.split()
    if len(words) <= 8:
        return text[:80]
    return " ".join(words[:8]) + "..." if len(words) > 8 else text[:80]


def _triggers_from_input(text: str, tool_names: list[str]) -> list[str]:
    phrases = []
    # Extract key phrases (3-5 word windows)
    words = text.split()
    for i in range(len(words) - 2):
        phrase = " ".join(words[i : i + 3])
        if len(phrase) > 10:
            phrases.append(phrase.lower())
    # Add tool names as triggers
    phrases.extend(tool_names)
    # Deduplicate and limit
    seen: set[str] = set()
    unique = []
    for p in phrases:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique[:8]


def _steps_from_tools(tool_names: list[str]) -> list[str]:
    steps = []
    for t in tool_names:
        if t == "web_search":
            steps.append("Search the web for relevant information")
        elif t == "read_file":
            steps.append("Read relevant files")
        elif t in ("write_file", "patch_file"):
            steps.append("Edit files as needed")
        elif t == "run_shell":
            steps.append("Run shell commands")
        elif t == "execute":
            steps.append("Execute Python code")
        elif t == "search_files":
            steps.append("Search files for relevant content")
        else:
            steps.append(f"Use {t}")
    return steps


def _draft_content(candidate: SkillCandidate) -> str:
    lines = [f"# {candidate.title}", ""]
    if candidate.workflow_steps:
        lines.append("## Workflow")
        lines.append("")
        for step in candidate.workflow_steps:
            lines.append(f"1. {step}")
        lines.append("")
    if candidate.verification_steps:
        lines.append("## Verification")
        lines.append("")
        for step in candidate.verification_steps:
            lines.append(f"- {step}")
        lines.append("")
    if candidate.safety_notes:
        lines.append("## Safety")
        lines.append("")
        lines.append(candidate.safety_notes)
        lines.append("")
    if candidate.required_tools:
        lines.append("## Required Tools")
        lines.append("")
        for t in candidate.required_tools:
            lines.append(f"- {t}")
        lines.append("")
    return "\n".join(lines)


def _name_from_title(title: str) -> str:
    import re
    name = title.lower().strip()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = name.strip("_")
    return name[:80] or "untitled_skill"
