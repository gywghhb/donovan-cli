from __future__ import annotations

from pathlib import Path

from donovanagent.skills.models import Skill, SkillType, SkillDraft, SkillCandidate
from donovanagent.skills.ranker import SkillRanker


def test_skill_dataclass_defaults() -> None:
    skill = Skill(name="test-skill", title="Test Skill", description="", content="Do X")
    assert skill.skill_type == SkillType.LEARNED
    assert skill.confidence == 1.0
    assert skill.usage_count == 0
    assert skill.triggers == []


def test_skill_types() -> None:
    assert SkillType.USER.value == "user"
    assert SkillType.LEARNED.value == "learned"
    assert SkillType.SYSTEM.value == "system"
    assert SkillType.PROJECT.value == "project"
    assert SkillType.DRAFT.value == "draft"


def test_skill_draft() -> None:
    draft = SkillDraft(
        name="draft-1",
        title="Draft One",
        description="A draft",
        content="Do Y",
        confidence=0.6,
    )
    assert draft.confidence == 0.6
    assert draft.trigger_phrases == []
    assert draft.required_tools == []


def test_skill_candidate_fields() -> None:
    cand = SkillCandidate(
        title="Fix bugs",
        trigger_phrases=["fix bug", "error fix"],
        workflow_steps=["step 1", "step 2"],
        required_tools=["bash", "read"],
        source_session="s-1",
    )
    assert cand.title == "Fix bugs"
    assert len(cand.trigger_phrases) == 2
    assert len(cand.required_tools) == 2
    assert cand.source_session == "s-1"


def test_ranker_default_score() -> None:
    ranker = SkillRanker()
    candidate = SkillCandidate(
        title="Generic",
        source_session="s-1",
    )
    score = ranker.score_candidate(candidate)
    # Base score is 0.5 minus vagueness penalty
    assert 0.0 <= score <= 1.0


def test_ranker_prefers_structured_skills() -> None:
    ranker = SkillRanker()
    vague = SkillCandidate(
        title="Vague",
        workflow_steps=["do stuff"],
        source_session="s-1",
    )
    structured = SkillCandidate(
        title="Structured",
        trigger_phrases=["fix bug", "error", "crash"],
        workflow_steps=["step 1", "step 2", "step 3", "step 4", "step 5"],
        required_tools=["bash", "read", "search"],
        verification_steps=["verify with test"],
        safety_notes="Be careful with rm",
        source_session="s-1",
    )
    assert ranker.score_candidate(structured) > ranker.score_candidate(vague)


def test_ranker_merge_tool_overlap() -> None:
    ranker = SkillRanker()
    existing = Skill(name="a", title="A", description="", content="x", tools=["bash", "read", "search"])
    candidate = SkillCandidate(
        title="B",
        required_tools=["bash", "read", "write"],
        source_session="s-1",
    )
    assert ranker.should_merge(existing, candidate) is True


def test_ranker_no_merge() -> None:
    ranker = SkillRanker()
    existing = Skill(name="a", title="A", description="", content="x", tools=["docker"])
    candidate = SkillCandidate(
        title="C",
        required_tools=["bash", "read"],
        source_session="s-1",
    )
    assert ranker.should_merge(existing, candidate) is False


def test_ranker_merge_trigger_overlap() -> None:
    ranker = SkillRanker()
    existing = Skill(name="a", title="A", description="", content="x", triggers=["fix bug"])
    candidate = SkillCandidate(
        title="B",
        trigger_phrases=["fix bug in code"],
        source_session="s-1",
    )
    assert ranker.should_merge(existing, candidate) is True


def test_ranker_rank_skills() -> None:
    ranker = SkillRanker()
    skills = [
        Skill(name="a", title="A", description="", content="x", triggers=["test"], usage_count=5, confidence=0.9, success_count=4, failure_count=1),
        Skill(name="b", title="B", description="", content="y", triggers=["other"], usage_count=1, confidence=0.5, success_count=1, failure_count=0),
    ]
    ranked = ranker.rank_skills(skills, "test", ["bash"])
    assert len(ranked) == 2
    assert ranked[0].name == "a"  # Higher score from matching trigger
