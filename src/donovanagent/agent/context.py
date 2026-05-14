from __future__ import annotations

import json
from typing import Any

from pathlib import Path

from donovanagent.agent.prompts import build_system_prompt
from donovanagent.agent.user_skills import load_user_skill_files
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.memory.database import MemoryDatabase
from donovanagent.memory.recall import recall_relevant
from donovanagent.memory.skills import LearnedSkill
from donovanagent.tools.registry import ToolRegistry

# Module-level caches — cleared when config or registry changes
_system_prompt_cache: tuple[int, str, str, str] | None = None  # (config_version, registry_key, mcp_key, prompt)
_user_skills_cache: tuple[Path, str, int, list[tuple[str, str]]] | None = None  # (config_dir, workspace, timestamp, skills)


def _cached_system_prompt(
    config: DonovanAgentConfig,
    registry: ToolRegistry,
    mcp_servers: list[dict[str, str | int | bool]] | None = None,
) -> str:
    """Build or return cached system prompt. Invalidates when config or tools change."""
    global _system_prompt_cache
    config_version = id(config) if hasattr(config, '_config_version') else id(config)
    registry_key = str([t.name for t in registry.enabled_tools()])
    mcp_key = str(sorted((s.get("name", ""), s.get("type", ""), s.get("connected", False)) for s in (mcp_servers or [])))
    if _system_prompt_cache is not None:
        cached_version, cached_key, cached_mcp, cached_prompt = _system_prompt_cache
        if cached_version == config_version and cached_key == registry_key and cached_mcp == mcp_key:
            return cached_prompt
    prompt = build_system_prompt(config, registry, mcp_servers=mcp_servers)
    _system_prompt_cache = (config_version, registry_key, mcp_key, prompt)
    return prompt


def _cached_user_skills(config_dir: Path | None, workspace: str) -> list[tuple[str, str]]:
    """Load and cache user .md skill files."""
    global _user_skills_cache
    if config_dir is None:
        return []
    if _user_skills_cache is not None:
        cached_dir, cached_ws, _, cached_data = _user_skills_cache
        if cached_dir == config_dir and cached_ws == workspace:
            return cached_data
    skills = load_user_skill_files(config_dir, workspace)
    _user_skills_cache = (config_dir, workspace, id(skills), skills)
    return skills


def clear_context_caches() -> None:
    """Clear all module-level caches. Call when config or registry changes."""
    global _system_prompt_cache, _user_skills_cache
    _system_prompt_cache = None
    _user_skills_cache = None


def build_messages(
    config: DonovanAgentConfig,
    db: MemoryDatabase,
    registry: ToolRegistry,
    session_id: str,
    user_input: str,
    learned_skills: list[LearnedSkill] | None = None,
    config_dir: Path | None = None,
    recalled_memories: list[str] | None = None,
    mcp_servers: list[dict[str, str | int | bool]] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": _cached_system_prompt(config, registry, mcp_servers)}]

    # Inject user-defined .md skill files
    if config_dir is not None:
        user_md_skills = _cached_user_skills(config_dir, config.app.default_workspace)
        if user_md_skills:
            blocks = "\n\n".join(f"## {name}\n{content}" for name, content in user_md_skills)
            messages.append({"role": "system", "content": "User-defined skills and instructions:\n\n" + blocks})

    if learned_skills:
        skill_text = "\n\n".join(
            f"Skill: {skill.name}\nPurpose: {skill.description}\nInstructions:\n{skill.content}"
            for skill in learned_skills
        )
        messages.append(
            {
                "role": "system",
                "content": "Relevant learned DonovanAgent skills for this turn:\n" + skill_text,
            }
        )
    if config.memory.enabled:
        recalled = recalled_memories if recalled_memories is not None else recall_relevant(db, user_input)
        if recalled:
            messages.append(
                {
                    "role": "system",
                    "content": "Potentially relevant memory snippets:\n" + "\n\n".join(recalled),
                }
            )

    # Inject conversation compact summary if one exists
    compacts = db.get_conversation_compacts(session_id)
    if compacts:
        latest = compacts[-1]
        compact_block = "Earlier conversation summary:\n" + latest["summary"]
        messages.append({"role": "system", "content": compact_block})

    history: list[dict[str, Any]] = []
    for row in db.recent_messages(session_id, limit=config.memory.max_context_messages):
        metadata = json.loads(row.get("metadata_json") or "{}")
        if metadata.get("skip_context"):
            continue
        role = row["role"]
        if role not in {"user", "assistant", "tool"}:
            continue
        item: dict[str, Any] = {"role": role, "content": row["content"]}
        if role == "assistant":
            raw_calls = metadata.get("tool_calls") or []
            if raw_calls:
                item["tool_calls"] = [
                    {
                        "id": str(c.get("id") or c.get("name") or "call"),
                        "type": "function",
                        "function": {
                            "name": str(c.get("name") or ""),
                            "arguments": json.dumps(c.get("arguments") or {}),
                        },
                    }
                    for c in raw_calls
                ]
        elif role == "tool":
            item["tool_call_id"] = metadata.get("tool_call_id") or ""
            item["name"] = metadata.get("tool_name") or ""
        history.append(item)

    messages.extend(_sanitize_tool_history(history))
    # Current user message is buffered (batch mode), not yet in DB — append it directly
    messages.append({"role": "user", "content": user_input})
    return messages


def _sanitize_tool_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove any tool/assistant pairs that would cause a 400 from the API:
    - Tool message with no preceding assistant+tool_calls (orphaned result)
    - Assistant+tool_calls message not followed by ALL expected tool results (incomplete group)
    """
    clean: list[dict[str, Any]] = []
    i = 0
    while i < len(history):
        item = history[i]

        if item["role"] == "tool":
            # Orphaned — no preceding assistant with tool_calls
            i += 1
            continue

        if item["role"] == "assistant" and item.get("tool_calls"):
            expected = {c["id"] for c in item["tool_calls"]}
            # Collect the immediately following tool messages
            j = i + 1
            tool_msgs: list[dict[str, Any]] = []
            while j < len(history) and history[j]["role"] == "tool":
                tool_msgs.append(history[j])
                j += 1
            covered = {m.get("tool_call_id", "") for m in tool_msgs}
            if expected and expected.issubset(covered):
                # Complete group — keep it
                clean.append(item)
                clean.extend(tool_msgs)
            # else: incomplete group — drop the whole block silently
            i = j
            continue

        clean.append(item)
        i += 1

    return clean
