from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from donovanagent.skills.models import (
    Skill,
    SkillCandidate,
    SkillDraft,
    SkillType,
    now_iso,
    skills_dir,
)
from donovanagent.memory.database import MemoryDatabase
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


_SKILL_DIRS = {
    SkillType.USER: "user",
    SkillType.LEARNED: "learned",
    SkillType.SYSTEM: "system",
    SkillType.PROJECT: "project",
    SkillType.DRAFT: "drafts",
}


class SkillManager:
    """Loads, searches, ranks, and persists skills across filesystem and SQLite."""

    def __init__(self, db: MemoryDatabase | None, workspace: str, config: Any) -> None:
        self.db = db
        self.workspace = workspace
        self.config = config
        self._cache: dict[str, Skill] = {}
        self._backward_compat_loaded = False

    def _base_dir(self, subdir: str) -> Path:
        return Path(self.workspace) / ".DonovanAgent" / "skills" / subdir

    def _ensure_dirs(self) -> None:
        for subdir in ("user", "learned", "system", "project", "drafts"):
            (Path(self.workspace) / ".DonovanAgent" / "skills" / subdir).mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[Skill]:
        """Load all skills from filesystem and SQLite."""
        skills: list[Skill] = []
        self._ensure_dirs()
        self._cache.clear()

        # Load user skills (.md files)
        for skill_type in (SkillType.USER, SkillType.LEARNED, SkillType.SYSTEM, SkillType.PROJECT, SkillType.DRAFT):
            d = self._base_dir(_SKILL_DIRS[skill_type])
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                skill = self._load_from_file(f, skill_type)
                if skill:
                    key = f"{skill_type.value}:{skill.name}"
                    self._cache[key] = skill
                    skills.append(skill)

        # Load SQLite learned skills
        if self.db is not None and hasattr(self.db, 'list_skills'):
            try:
                rows = self.db.list_skills()
                for row in rows:
                    name = str(row.get("name", ""))
                    key = f"learned:{name}"
                    if key not in self._cache:
                        skill = Skill(
                            id=int(row.get("id", 0)),
                            name=name,
                            title=name.replace("_", " ").title(),
                            description=str(row.get("description", "")),
                            content=str(row.get("content", "")),
                            skill_type=SkillType.LEARNED,
                            usage_count=int(row.get("uses", 0)),
                            created_at=str(row.get("created_at", "")),
                            updated_at=str(row.get("updated_at", "")),
                        )
                        self._cache[key] = skill
                        skills.append(skill)
            except Exception as exc:
                logger.debug("Error loading SQLite skills: %s", exc)

        # Backward compat: load from .DonovanAgent/skills/*.md (direct)
        if not self._backward_compat_loaded:
            legacy = Path(self.workspace) / ".DonovanAgent" / "skills"
            if legacy.is_dir():
                for f in sorted(legacy.glob("*.md")):
                    if f.parent.name != f.parent.name and any(
                        f.parent.name == p for p in ("user", "learned", "system", "project", "drafts")
                    ):
                        continue
                    key = f"user:{f.stem}"
                    if key in self._cache:
                        continue
                    skill = self._load_from_file(f, SkillType.USER)
                    if skill:
                        self._cache[key] = skill
                        skills.append(skill)
            self._backward_compat_loaded = True

        return skills

    def _load_from_file(self, path: Path, skill_type: SkillType) -> Skill | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        name = path.stem
        title = name.replace("_", " ").title()
        description = ""
        content = text
        triggers: list[str] = []
        tools: list[str] = []
        workflow: list[str] = []
        verification: list[str] = []
        safety = ""
        confidence = 1.0
        usage = 0
        success = 0
        failure = 0
        source_ids: list[str] = []
        created = ""
        updated = ""

        # Parse YAML frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    meta = yaml.safe_load(parts[1]) or {}
                    name = str(meta.get("name", name))
                    title = str(meta.get("title", title))
                    description = str(meta.get("description", description))
                    triggers = meta.get("triggers", triggers)
                    tools = meta.get("tools", tools)
                    workflow = meta.get("workflow", workflow)
                    verification = meta.get("verification", verification)
                    safety = str(meta.get("safety_notes", ""))
                    confidence = float(meta.get("confidence", 1.0))
                    usage = int(meta.get("usage_count", 0))
                    success = int(meta.get("success_count", 0))
                    failure = int(meta.get("failure_count", 0))
                    source_ids = meta.get("source_session_ids", source_ids)
                    created = str(meta.get("created_at", created))
                    updated = str(meta.get("updated_at", updated))
                    content = parts[2].strip()
                except Exception as exc:
                    logger.debug("Error parsing frontmatter for %s: %s", path, exc)

        return Skill(
            name=name, title=title, description=description, content=content,
            skill_type=skill_type, triggers=triggers, tools=tools,
            workflow_steps=workflow, verification_steps=verification,
            safety_notes=safety, confidence=confidence,
            usage_count=usage, success_count=success, failure_count=failure,
            source_session_ids=source_ids, created_at=created, updated_at=updated,
        )

    def search(self, query: str, limit: int = 5) -> list[Skill]:
        """Search skills by trigger phrase, name, and content matching."""
        skills = self.load_all() if not self._cache else list(self._cache.values())
        query_lower = query.lower()
        scored: list[tuple[float, Skill]] = []

        for skill in skills:
            score = 0.0
            # Trigger phrase match (highest)
            for trigger in skill.triggers:
                if trigger.lower() in query_lower:
                    score += 3.0
            # Name match
            if skill.name.lower() in query_lower or query_lower in skill.name.lower():
                score += 2.0
            # Description match
            if query_lower in skill.description.lower():
                score += 1.5
            # Content match
            if query_lower in skill.content.lower():
                score += 1.0
            # Usage/reliability boost
            total_uses = skill.success_count + skill.failure_count
            if total_uses > 0:
                success_rate = skill.success_count / total_uses
                score += success_rate * 0.5
            score += min(skill.usage_count * 0.1, 1.0)
            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:limit]]

    def find_by_trigger(self, tools: list[str], query: str = "") -> list[Skill]:
        """Find skills whose trigger phrases match the current context."""
        skills = self.load_all() if not self._cache else list(self._cache.values())
        results: list[Skill] = []

        for skill in skills:
            # Match by tools
            tool_match = any(t in skill.tools for t in tools) if skill.tools else False
            # Match by trigger phrase
            phrase_match = False
            if query:
                ql = query.lower()
                phrase_match = any(t.lower() in ql or ql in t.lower() for t in skill.triggers)

            if tool_match or phrase_match:
                results.append(skill)

        return results

    def save_file(self, skill: Skill) -> bool:
        """Save a skill to filesystem as .md with YAML frontmatter."""
        subdir = _SKILL_DIRS.get(skill.skill_type, "user")
        d = self._base_dir(subdir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{skill.name}.md"

        meta: dict[str, Any] = {
            "name": skill.name,
        }
        if skill.title:
            meta["title"] = skill.title
        if skill.description:
            meta["description"] = skill.description
        if skill.triggers:
            meta["triggers"] = skill.triggers
        if skill.tools:
            meta["tools"] = skill.tools
        if skill.workflow_steps:
            meta["workflow"] = skill.workflow_steps
        if skill.verification_steps:
            meta["verification"] = skill.verification_steps
        if skill.safety_notes:
            meta["safety_notes"] = skill.safety_notes
        if skill.confidence != 1.0:
            meta["confidence"] = round(skill.confidence, 2)
        if skill.usage_count:
            meta["usage_count"] = skill.usage_count
        if skill.success_count:
            meta["success_count"] = skill.success_count
        if skill.failure_count:
            meta["failure_count"] = skill.failure_count
        if skill.source_session_ids:
            meta["source_session_ids"] = skill.source_session_ids
        now = now_iso()
        meta["created_at"] = skill.created_at or now
        meta["updated_at"] = now

        try:
            frontmatter = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
            path.write_text(f"---\n{frontmatter}---\n\n{skill.content}\n", encoding="utf-8")
            return True
        except OSError as exc:
            logger.error("Failed to save skill %s: %s", skill.name, exc)
            return False

    def save_draft(self, draft: SkillDraft) -> bool:
        """Save a skill draft to filesystem."""
        skill = Skill(
            name=draft.name,
            title=draft.title,
            description=draft.description or "",
            content=draft.content,
            skill_type=SkillType.DRAFT,
            triggers=draft.trigger_phrases,
            tools=draft.required_tools,
            workflow_steps=draft.workflow_steps,
            verification_steps=draft.verification_steps,
            safety_notes=draft.safety_notes,
            confidence=draft.confidence,
            source_session_ids=[draft.source_session_id] if draft.source_session_id else [],
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        ok = self.save_file(skill)
        # Also save to SQLite skill_drafts
        if ok and self.db is not None and hasattr(self.db, 'add_skill_draft'):
            try:
                self.db.add_skill_draft(
                    name=draft.name, title=draft.title, description=draft.description,
                    content=draft.content, trigger_phrases=draft.trigger_phrases,
                    workflow_steps=draft.workflow_steps, required_tools=draft.required_tools,
                    verification_steps=draft.verification_steps, safety_notes=draft.safety_notes,
                    confidence=draft.confidence, reason=draft.reason,
                    source_session_id=draft.source_session_id,
                )
            except Exception as exc:
                logger.debug("Failed to save draft to SQLite: %s", exc)
        return ok

    def promote_draft(self, name: str) -> bool:
        """Move a draft skill to learned."""
        draft = self._load_from_file(self._base_dir("drafts") / f"{name}.md", SkillType.DRAFT)
        if not draft:
            return False
        draft.skill_type = SkillType.LEARNED
        draft.confidence = max(draft.confidence, 0.8)
        draft.updated_at = now_iso()
        # Remove draft file
        draft_path = self._base_dir("drafts") / f"{name}.md"
        try:
            draft_path.unlink(missing_ok=True)
        except OSError:
            pass
        return self.save_file(draft)

    def delete_skill(self, name: str, skill_type: SkillType = SkillType.LEARNED) -> bool:
        """Delete a skill from filesystem."""
        subdir = _SKILL_DIRS.get(skill_type, "user")
        path = self._base_dir(subdir) / f"{name}.md"

        # Also check legacy path
        legacy = Path(self.workspace) / ".DonovanAgent" / "skills" / f"{name}.md"

        deleted = False
        for p in (path, legacy):
            if p.exists():
                try:
                    p.unlink()
                    deleted = True
                except OSError:
                    pass

        # Remove from cache
        key = f"{skill_type.value}:{name}"
        self._cache.pop(key, None)
        # Also try all types
        for st in SkillType:
            self._cache.pop(f"{st.value}:{name}", None)

        return deleted

    def update_usage(self, skill: Skill, success: bool = True) -> None:
        """Update usage metrics for a skill."""
        skill.usage_count += 1
        if success:
            skill.success_count += 1
        else:
            skill.failure_count += 1
        skill.updated_at = now_iso()
        self.save_file(skill)
        # Update SQLite
        if self.db is not None and hasattr(self.db, 'add_skill_usage'):
            try:
                self.db.add_skill_usage(
                    skill_id=skill.id if isinstance(skill.id, int) else 0,
                    session_id="", success=success,
                )
            except Exception as exc:
                logger.debug("Failed to log skill usage: %s", exc)

    def get_skills_for_prompt(self, query: str, tools: list[str], limit: int | None = None) -> list[Skill]:
        """Get the most relevant skills to inject into the system prompt."""
        if limit is None:
            limit = self.config.skills.max_injected_skills if hasattr(self.config, "skills") else 5

        # Find by direct search and trigger matching
        by_search = self.search(query, limit=limit * 2)
        by_trigger = self.find_by_trigger(tools, query)

        # Merge and de-duplicate
        seen: set[str] = set()
        ranked: list[Skill] = []
        for skill in by_search + by_trigger:
            key = f"{skill.skill_type.value}:{skill.name}"
            if key not in seen:
                seen.add(key)
                ranked.append(skill)

        # Skip drafts for prompt injection
        ranked = [s for s in ranked if s.skill_type != SkillType.DRAFT]
        return ranked[:limit]

    def list_all(self) -> list[Skill]:
        """List all available skills."""
        self._cache.clear()
        return self.load_all()

    def list_drafts(self) -> list[Skill]:
        """List all draft skills."""
        return [s for s in self.list_all() if s.skill_type == SkillType.DRAFT]
