from __future__ import annotations

import re
from typing import Any

from donovanagent.skills.models import Skill, SkillCandidate
from donovanagent.memory.database import MemoryDatabase


class SkillRanker:
    """Ranks and scores skill candidates and existing skills."""

    def __init__(self, db: MemoryDatabase | None = None) -> None:
        self.db = db

    def score_candidate(self, candidate: SkillCandidate) -> float:
        """Score a skill candidate from 0.0 to 1.0."""
        score = 0.5  # Base
        factors = []

        # Multiple workflow steps â†’ more valuable
        if len(candidate.workflow_steps) >= 5:
            factors.append(("many_steps", 0.15))
        elif len(candidate.workflow_steps) >= 3:
            factors.append(("moderate_steps", 0.1))
        elif len(candidate.workflow_steps) >= 1:
            factors.append(("few_steps", 0.05))

        # Has verification
        if candidate.verification_steps:
            factors.append(("has_verification", 0.1))

        # Has safety notes
        if candidate.safety_notes:
            factors.append(("has_safety", 0.05))

        # Multiple tools
        if len(candidate.required_tools) >= 3:
            factors.append(("multi_tool", 0.1))
        elif len(candidate.required_tools) >= 1:
            factors.append(("has_tools", 0.05))

        # Trigger phrases indicate specificity
        if len(candidate.trigger_phrases) >= 3:
            factors.append(("good_triggers", 0.1))
        elif candidate.trigger_phrases:
            factors.append(("has_triggers", 0.05))

        # Penalize vague candidates
        if len(candidate.workflow_steps) <= 1:
            factors.append(("too_vague", -0.2))

        # Penalize very short content
        content_len = len(candidate.safety_notes) + sum(len(s) for s in candidate.workflow_steps)
        if content_len < 50:
            factors.append(("too_short", -0.1))

        for _, delta in factors:
            score = max(0.0, min(1.0, score + delta))

        return round(score, 2)

    def rank_skills(self, skills: list[Skill], query: str, tools: list[str]) -> list[Skill]:
        """Rank existing skills by relevance to current context."""
        scored: list[tuple[float, Skill]] = []
        for skill in skills:
            score = 0.0
            # Trigger phrase match
            if query:
                ql = query.lower()
                phrase_match = any(t.lower() in ql or ql in t.lower() for t in skill.triggers)
                if phrase_match:
                    score += 3.0
            # Tool match
            tool_match = len([t for t in skill.tools if t in tools]) if skill.tools else 0
            score += tool_match * 0.5
            # Success rate
            total = skill.success_count + skill.failure_count
            if total > 0:
                score += (skill.success_count / total) * 1.0
            # Usage
            score += min(skill.usage_count * 0.2, 1.0)
            # Confidence
            score += skill.confidence * 0.5

            scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored]

    def should_merge(self, existing: Skill, candidate: SkillCandidate) -> bool:
        """Determine if a candidate should merge into an existing skill."""
        # Same tool set
        existing_tools = set(existing.tools)
        candidate_tools = set(candidate.required_tools)
        if existing_tools and candidate_tools:
            overlap = len(existing_tools & candidate_tools)
            if overlap >= len(existing_tools) * 0.5:
                return True
        # Same trigger phrases
        for t in candidate.trigger_phrases:
            for et in existing.triggers:
                if t.lower() in et.lower() or et.lower() in t.lower():
                    return True
        return False
