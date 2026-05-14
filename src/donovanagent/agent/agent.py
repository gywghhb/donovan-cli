from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from rich.console import Console

from donovanagent.activity import ActivityService
from donovanagent.agent.compaction import CompactionService
from donovanagent.agent.context import build_messages
from donovanagent.agent.planner import derive_title
from donovanagent.agent.self_improvement import SelfImprovementLoop
from donovanagent.agent.tool_protocol import (
    assistant_message_with_tool_calls,
    parse_fallback_tool_call,
)
from donovanagent.agent.user_skills import load_user_skill_files
from donovanagent.browser import BrowserService
from donovanagent.checkpoints import CheckpointManager
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.execution import BackendManager
from donovanagent.memory.database import MemoryDatabase
from donovanagent.memory.manager import MemoryManager
from donovanagent.memory.recall import recall_relevant
from donovanagent.memory.skills import LearnedSkill, recall_skills
from donovanagent.memory.summaries import generate_session_summary
from donovanagent.planning import PlanManager
from donovanagent.providers.base import LLMProvider
from donovanagent.providers.models import ChatResponse, ToolCall
from donovanagent.providers.registry import build_provider
from donovanagent.mcp.manager import McpManager
from donovanagent.scheduler import SchedulerService
from donovanagent.skills import SkillManager
from donovanagent.skills.learner import SkillLearner
from donovanagent.skills.ranker import SkillRanker
from donovanagent.subagents import SubagentManager
from donovanagent.thinking import ThinkingManager
from donovanagent.tools.approval import ApprovalManager
from donovanagent.tools.base import ToolExecutionContext
from donovanagent.tools.registry import ToolRegistry
from donovanagent.ui.render import plain_text, strip_tool_markup
from donovanagent.tools.mcp_tools import parse_dsml_tool_calls, extract_internal_tool_calls
from donovanagent.utils.errors import MaxIterationsReached, ProviderError
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

# Regex patterns for detecting incomplete/intermediate planning text
# that should not be treated as a final answer.
_TASK_INCOMPLETE_INDICATORS_RE = re.compile(
    r"^(Step\s+\d+|Let me\s+(now|try|check|see|look|find|search|"
    r"verify|confirm|investigate|start|begin|continue)|"
    r"I['']ll\s+(now|try|check|see|look|find|search|"
    r"verify|confirm|investigate|start|begin|continue|next|then)|"
    r"(Next|Then|Now|First|Second|Third|Finally|Lastly),\s+I['']?ll|"
    r"(Next|Then|Now|First|Second|Third|Finally|Lastly),\s+let me|"
    r"I\s+(still\s+)?need\s+to|"
    r"I\s+(should|could|will|would|can)\s+(now\s+)?(try|check|see|look|find|search|run|execute|call|use|invoke)|"
    r"My\s+(next\s+)?step\s+(is|will|should)|"
    r"Let's\s+(try|check|see|look|find|search|run|execute|start|begin|continue|move))",
    re.IGNORECASE,
)
# Detect "Finding...", "Searching...", "Analyzing..." as standalone lines
_TASK_INCOMPLETE_LINE_RE = re.compile(
    r"^(Finding|Searching|Analyzing|Looking|Checking|Investigating)"
    r"\s+(for|the|a|some|into)\s",
    re.IGNORECASE,
)

# Intent categories for task completion checking
_INTENT_READ = frozenset({"read", "question", "explain", "describe", "show", "what", "how", "why"})
_INTENT_MODIFY = frozenset({
    "write", "edit", "update", "create", "add", "change", "modify", "patch",
    "delete", "remove", "rename", "fix", "refactor", "replace",
})
_INTENT_SEARCH = frozenset({"search", "find", "research", "lookup", "explore"})
_INTENT_SHELL = frozenset({"run", "execute", "build", "compile", "test", "deploy"})
_INTENT_MCP = frozenset({"mcp", "connect", "call_tool"})


_SAFE_SUMMARIES = {
    "searching": "Searching for relevant information...",
    "analyzing": "Analyzing the code...",
    "planning": "Planning the approach...",
    "editing": "Applying changes to the code...",
    "testing": "Running tests to verify...",
    "verifying": "Verifying the changes...",
    "investigating": "Investigating the issue...",
}


def _loop_signature(call: ToolCall) -> str:
    arg_keys = sorted(call.arguments.keys()) if call.arguments else []
    return f"{call.name}({','.join(arg_keys)})"


def _detect_loop(history: list[str], max_identical: int = 5, max_repeating: int = 3) -> str | None:
    """Detect repeating tool call patterns.

    Args:
        history: List of tool call signatures.
        max_identical: Max consecutive identical calls before loop detection.
        max_repeating: Max pattern repetitions before loop detection.

    Returns a description of the detected loop, or None if no loop found.
    """
    if len(history) < 4:
        return None

    # 1) Consecutive identical calls (e.g. same tool called max_identical+ times)
    last = history[-1]
    count = 0
    for entry in reversed(history):
        if entry == last:
            count += 1
        else:
            break
    if count >= max_identical:
        return f"same tool call repeated {count} times: {last}"

    # 2) Repeating pattern of 2+ calls detected max_repeating+ times
    for pattern_len in range(2, min(4, len(history) // 3 + 1)):
        pattern = history[-pattern_len:]
        repeats = 1
        start = len(history) - pattern_len
        while start - pattern_len >= 0:
            segment = history[start - pattern_len:start]
            if segment == pattern:
                repeats += 1
                start -= pattern_len
            else:
                break
        if repeats >= max_repeating:
            return f"pattern repeated {repeats}x: {pattern}"

    return None


class DonovanAgent:
    def __init__(
        self,
        config: DonovanAgentConfig,
        db: MemoryDatabase,
        provider: LLMProvider,
        registry: ToolRegistry,
        console: Console,
        approval: ApprovalManager,
        *,
        max_tool_iterations: int | None = None,
        config_dir: Any = None,
    ) -> None:
        self.config = config
        self.db = db
        self.provider = provider
        self.registry = registry
        self.console = console
        self.approval = approval
        # Use configurable budget, fall back to 80
        agent_cfg = config.agent
        self._max_iterations = max_tool_iterations or agent_cfg.max_steps
        self._max_identical = 5  # consecutive identical tool calls
        self._max_repeating = 3  # pattern repetitions
        self._max_same_tool_retries = agent_cfg.max_same_tool_retries
        self._suppress_planning = agent_cfg.suppress_intermediate_planning
        self._config_dir = config_dir
        self.last_tool_names: list[str] = []
        self.last_context_tokens = 0
        self.tool_callback: Callable[[str, dict], None] | None = None
        self.state_callback: Callable[[str], None] | None = None
        self.indicator: Any | None = None  # ActivityIndicator for step tree display
        self._last_state_update: float = 0.0  # throttle for state updates

        # Activity stream
        self.activity = ActivityService(db, config)
        self.activity_renderer = None  # set by app if needed

        # Thinking
        self.thinking = ThinkingManager(config)

        # Planning
        self.plan_manager = PlanManager(config)

        # Checkpoints
        self.checkpoints = CheckpointManager(config, config.app.default_workspace)

        # Execution backend
        self.backend_manager = BackendManager(config)

        # Memory manager
        self.memory_manager = MemoryManager(db, config)

        # Skill manager
        self.skill_manager = SkillManager(db, config.app.default_workspace, config)
        self.skill_ranker = SkillRanker(db)
        self.skill_learner = SkillLearner(self.skill_manager, self.skill_ranker, config)

        # Self-improvement
        self.self_improvement = SelfImprovementLoop(config, db, provider)

        # Subagents
        self.subagent_manager = SubagentManager(
            config,
            provider_factory=lambda: build_provider(config),
            tool_schema_filter=self._filter_tool_schemas,
        )

        # Browser
        self.browser_service = BrowserService(config)

        # Scheduler
        self.scheduler = SchedulerService(db, config)

        # MCP (Model Context Protocol)
        self.mcp_manager = McpManager(
            config, registry,
            project_dir=config.app.default_workspace,
        )

        # Conversation compaction
        self.compaction = CompactionService(db, config)

        # Streaming state
        self._streaming_tools = bool(getattr(config.agent, 'streaming_tools', True))
        self._turn_count = 0
        self._last_backend: str | None = None

    def start_session(self) -> str:
        return self.db.create_session(
            workspace=self.config.app.default_workspace,
            provider=self.config.provider.active,
            model=self.config.provider.model,
        )

    def run_turn(self, session_id: str, user_input: str) -> str:
        self.last_tool_names = []
        self._turn_recalled_memories = None
        self.db.open_batch()
        try:
            return self._run_turn_internal(session_id, user_input)
        finally:
            self.db.close_batch()

    def _run_turn_internal(self, session_id: str, user_input: str) -> str:
        self.last_tool_names = []
        self._turn_recalled_memories = None
        first_messages = self.db.recent_messages(session_id, limit=1)
        self.db.add_message(session_id, "user", user_input)
        if not first_messages:
            title = derive_title(user_input)
            self.db.update_session_title(session_id, title)

        # Memory recall - cached to avoid re-querying in build_messages
        recalled_memories: list[str] | None = None
        if self.config.memory.auto_recall and self.config.memory.enabled:
            if len(user_input.split()) >= 4:
                self.activity.memory_recall_started(session_id=session_id)
                if self.state_callback:
                    self.state_callback("Recalling")
                recalled_memories = recall_relevant(self.db, user_input)
                if recalled_memories:
                    self.activity.emit(
                        "memory_recall_completed",
                        message=f"Found {len(recalled_memories)} relevant memories",
                        session_id=session_id,
                    )
        self._turn_recalled_memories = recalled_memories

        # Skill recall
        improvement_context = self.self_improvement.before_turn(user_input)
        if improvement_context.recalled_skills:
            for skill in improvement_context.recalled_skills:
                self.activity.skill_loaded(
                    name=skill.name,
                    session_id=session_id,
                )

        # Plan mode check
        if self.plan_manager.is_active:
            self.activity.planning_started(session_id=session_id)
            self.activity.planning_finished(
                item_count=len(self.plan_manager.current_plan.items) if self.plan_manager.current_plan else 0,
                session_id=session_id,
            )

        # Thinking
        if self.thinking.should_show_summaries():
            self.activity.thinking_started(session_id=session_id)
            if self.state_callback:
                self.state_callback("Thinking")

        # Backend - only emit on change to reduce noise
        current_backend = self.backend_manager.active_name
        if current_backend != self._last_backend:
            self.activity.backend_selected(
                backend=current_backend,
                session_id=session_id,
            )
            self._last_backend = current_backend

        # Build MCP servers status list for system prompt
        mcp_servers = [
            {"name": s.name, "type": s.type, "connected": s.connected, "trust": s.trust}
            for s in self.mcp_manager.list_statuses()
        ]

        messages = build_messages(
            self.config, self.db, self.registry, session_id, user_input,
            learned_skills=improvement_context.recalled_skills,
            config_dir=self._config_dir,
            recalled_memories=recalled_memories,
            mcp_servers=mcp_servers,
        )
        self.last_context_tokens = estimate_tokens_from_messages(messages)

        # Conversation compaction
        if self.compaction.should_compact(self.last_context_tokens):
            all_msgs = self.db.recent_messages(session_id, limit=9999)
            result = self.compaction.compact(session_id, all_msgs, activity=self.activity)
            if result is not None:
                logger.info(
                    "Compaction triggered: compacted %d messages, summary=%d tokens",
                    result.compacted_count, result.summary_token_count,
                )
                messages = build_messages(
                    self.config, self.db, self.registry, session_id, user_input,
                    learned_skills=improvement_context.recalled_skills,
                    config_dir=self._config_dir,
                    recalled_memories=recalled_memories,
                    mcp_servers=mcp_servers,
                )
                self.last_context_tokens = estimate_tokens_from_messages(messages)

        tool_schemas = self.registry.openai_schemas()
        loop_history: list[str] = []

        use_streaming = self._streaming_tools and tool_schemas

        if use_streaming:
            return self._run_streaming_turn(
                session_id, user_input, messages, tool_schemas,
                loop_history, improvement_context,
            )

        return self._run_tool_loop(
            session_id, user_input, messages, tool_schemas,
            loop_history, improvement_context,
        )

    def _filter_tool_schemas(self, tool_names: list[str]) -> list[dict[str, Any]]:
        return [
            self.registry.get(name).openai_schema()
            for name in tool_names
            if name in self.registry._tools
        ]

    def _run_streaming_turn(
        self, session_id: str, user_input: str, messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]], loop_history: list[str],
        improvement_context: Any,
    ) -> str:
        """Run a streaming turn that can interleave tool calls."""
        registered_names = set()
        if hasattr(self.registry, '_tools'):
            registered_names = set(self.registry._tools.keys())

        try:
            response = self.provider.chat(messages, tools=tool_schemas if tool_schemas else None)
            if response.prompt_tokens:
                self.last_context_tokens = response.prompt_tokens
        except ProviderError as exc:
            if tool_schemas and "tool" in str(exc).lower():
                response = self.provider.chat(messages, tools=None, tool_choice=None)
                if response.prompt_tokens:
                    self.last_context_tokens = response.prompt_tokens
            else:
                raise
        fallback = parse_fallback_tool_call(response.content)
        tool_calls = response.tool_calls or ([fallback] if fallback else [])

        # DSML fallback with name repair
        if not tool_calls:
            dsml_calls = parse_dsml_tool_calls(response.content or "")
            if not dsml_calls and response.content:
                _, dsml_calls = extract_internal_tool_calls(
                    response.content, registered_names=registered_names,
                )
            if dsml_calls:
                tool_calls = [
                    ToolCall(id=f"dsml_{c['name']}", name=c["name"], arguments=c["arguments"])
                    for c in dsml_calls
                ]

        if not tool_calls:
            content = plain_text(response.content)
            content = self._final_sanitize(content)
            if self._is_intermediate_planning(content):
                return self._run_tool_loop(
                    session_id, user_input, messages, tool_schemas,
                    loop_history, improvement_context,
                    initial_response=response,
                )
            self.db.add_message(session_id, "assistant", content)
            if not response.prompt_tokens:
                self.last_context_tokens = estimate_tokens_from_messages(messages)
            self._after_turn(
                session_id, user_input, content,
                self.last_tool_names, improvement_context,
            )
            self.activity.task_completed(message=content[:200], session_id=session_id)
            self.activity.flush()
            return content

        return self._run_tool_loop(
            session_id, user_input, messages, tool_schemas,
            loop_history, improvement_context,
            initial_response=response,
        )

    def _is_intermediate_planning(self, text: str) -> bool:
        """Check if text looks like intermediate planning, not a final answer."""
        if not text or not text.strip():
            return False
        stripped = text.strip()
        first_line = stripped.split("\n")[0][:100]
        if _TASK_INCOMPLETE_INDICATORS_RE.search(first_line):
            return True
        if _TASK_INCOMPLETE_LINE_RE.search(first_line):
            return True
        return False

    def _classify_intent(self, user_input: str) -> str:
        """Classify the user's request into an intent category."""
        words = set(user_input.lower().split())
        if words & _INTENT_MODIFY:
            return "modify"
        if words & _INTENT_SHELL:
            return "shell"
        if words & _INTENT_SEARCH:
            return "search"
        if words & _INTENT_READ:
            return "read"
        if words & _INTENT_MCP:
            return "mcp"
        return "other"

    def _is_task_complete(
        self, intent: str, tool_names: list[str], content: str,
    ) -> bool:
        """Heuristic check: is the task reasonably complete?

        Returns True if the task appears complete based on intent, tools used,
        and whether the response looks like intermediate planning.

        The primary signal is the content itself: if it doesn't look like
        intermediate planning text, the task is considered complete.
        Intent-specific tool checks are deliberately loose to avoid forcing
        the model into unnecessary re-searching or tool calls when it can
        already answer from context.
        """
        if self._is_intermediate_planning(content):
            return False

        # If the model never called any tools, still allow completion —
        # it may have already known the answer or the task may be trivial.
        if not tool_names:
            return True

        names_set = set(tool_names)

        # For modify intent, require at least one mutating tool was used,
        # otherwise the model probably just talked about modifying.
        if intent == "modify":
            mutating = {"write_file", "patch_file", "execute", "run_shell"}
            if not (names_set & mutating):
                return False

        return True

    def _final_sanitize(self, text: str) -> str:
        """Last-resort sanitizer: ensure no raw DSML markup reaches the user."""
        if not text:
            return text

        text = re.sub(
            r'<tool_calls[^>]*>.*?</tool_calls\s*>',
            '', text, flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r'<invoke\s+[^>]*>.*?</invoke\s*>',
            '', text, flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r'</?invoke[^>]*>', '', text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r'</?parameter[^>]*>', '', text, flags=re.IGNORECASE,
        )
        text = re.sub(
            r'<function\s+[^>]*>.*?</function>',
            '', text, flags=re.DOTALL | re.IGNORECASE,
        )
        text = re.sub(
            r'\{"type":\s*"(?:tool_call|function)".*?"arguments"\s*:\s*\{.*?\}\s*\}',
            '', text, flags=re.DOTALL,
        )
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _run_tool_loop(
        self, session_id: str, user_input: str, messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]], loop_history: list[str],
        improvement_context: Any, initial_response: ChatResponse | None = None,
    ) -> str:
        """Run the tool loop until task completion, budget exhaustion, or block.

        The loop continues as long as the model produces tool calls (native or
        DSML) and the task is not yet complete. It stops only when:
        - The task is complete (heuristic check)
        - The execution budget is exceeded
        - The user denies a required permission
        - An unrecoverable error occurs
        """
        intent = self._classify_intent(user_input)
        total_tool_calls = 0
        same_tool_count = 0
        last_tool_name: str | None = None
        registered_names = set()
        if hasattr(self.registry, '_tools'):
            registered_names = set(self.registry._tools.keys())

        for iteration in range(self._max_iterations):
            if self.state_callback:
                now = time.monotonic()
                if now - self._last_state_update > 0.5:
                    self.state_callback("Thinking")
                    self._last_state_update = now

            if iteration == 0 and initial_response:
                response = initial_response
                if not response.prompt_tokens:
                    self.last_context_tokens = estimate_tokens_from_messages(messages)
            else:
                try:
                    response = self.provider.chat(messages, tools=tool_schemas if tool_schemas else None)
                    if response.prompt_tokens:
                        self.last_context_tokens = response.prompt_tokens
                except ProviderError as exc:
                    if tool_schemas and "tool" in str(exc).lower():
                        response = self.provider.chat(messages, tools=None, tool_choice=None)
                        if response.prompt_tokens:
                            self.last_context_tokens = response.prompt_tokens
                    else:
                        raise

            fallback = parse_fallback_tool_call(response.content)
            tool_calls = response.tool_calls or ([fallback] if fallback else [])

            # DSML fallback with name repair
            dsml_detected_this_turn = False
            if not tool_calls:
                dsml_calls = parse_dsml_tool_calls(response.content or "")
                if not dsml_calls and response.content:
                    _, dsml_calls = extract_internal_tool_calls(
                        response.content, registered_names=registered_names,
                    )
                if dsml_calls:
                    tool_calls = [
                        ToolCall(
                            id=f"dsml_{c['name']}_{iteration}",
                            name=c["name"], arguments=c["arguments"],
                        )
                        for c in dsml_calls
                        if c.get("name") and c.get("arguments") is not None
                    ]
                    if tool_calls:
                        dsml_detected_this_turn = True

            # CASE 1: No tool calls - check if task is complete or needs continuation
            if not tool_calls:
                content = plain_text(response.content or "")
                content = self._final_sanitize(content)

                if self._is_intermediate_planning(content):
                    msg = (
                        "[System: You produced intermediate planning text instead of a final "
                        "answer and did not call any tools. If you still need to do something, "
                        "call the appropriate tool. If the task is finished, provide a concise "
                        "final answer without step-by-step narration.]"
                    )
                    messages.append({"role": "user", "content": msg})
                    self.db.add_message(session_id, "user", msg)
                    continue

                if self._is_task_complete(intent, self.last_tool_names, content):
                    if not content:
                        content = "Task complete."
                    self.db.add_message(session_id, "assistant", content)
                    if not response.prompt_tokens:
                        self.last_context_tokens = estimate_tokens_from_messages(messages)
                    self._after_turn(
                        session_id, user_input, content,
                        self.last_tool_names, improvement_context,
                    )
                    if self.plan_manager.is_active:
                        self.plan_manager.complete_plan(summary=content[:200])
                    self.activity.task_completed(message=content[:200], session_id=session_id)
                    self.activity.flush()
                    return content

                msg = (
                    "[System: You provided text but did not call any tools. "
                    "The task is not yet complete based on what was requested. "
                    "Continue working by calling the appropriate tool(s). "
                    "Call a tool or provide a final answer only when done.]"
                )
                messages.append({"role": "user", "content": msg})
                self.db.add_message(session_id, "user", msg)
                continue

            # Track same-tool retry limit
            for call in tool_calls:
                if call.name == last_tool_name:
                    same_tool_count += 1
                else:
                    same_tool_count = 1
                    last_tool_name = call.name
                if same_tool_count > self._max_same_tool_retries:
                    msg = (
                        f"[System: You called {call.name} {same_tool_count} times in a row. "
                        "Try a different approach or provide a final answer.]"
                    )
                    messages.append({"role": "user", "content": msg})
                    self.db.add_message(session_id, "user", msg)
                    continue

            # Track call signatures and check for loops
            for call in tool_calls:
                loop_history.append(_loop_signature(call))
            loop_reason = _detect_loop(loop_history, self._max_identical, self._max_repeating)
            if loop_reason:
                msg = (
                    f"[System: Loop detected ({loop_reason}). "
                    "You are repeating the same tool calls without making progress. "
                    "Give a final answer now based on what you already know. "
                    "Do not call any more tools.]"
                )
                messages.append({"role": "user", "content": msg})
                self.db.add_message(session_id, "user", msg)
                try:
                    response = self.provider.chat(messages, tools=None, tool_choice=None)
                except ProviderError:
                    raise MaxIterationsReached()
                content = plain_text(response.content)
                content = self._final_sanitize(content)
                self.db.add_message(session_id, "assistant", content)
                self._after_turn(
                    session_id, user_input, content,
                    self.last_tool_names, improvement_context,
                )
                if self.plan_manager.is_active:
                    self.plan_manager.complete_plan(summary=content[:200])
                self.activity.task_completed(message=content[:200], session_id=session_id)
                self.activity.flush()
                return content

            # Pre-warning when approaching the iteration limit
            remaining = self._max_iterations - iteration
            if remaining <= 10 and remaining > 0 and remaining % 5 == 0:
                pre_warning = (
                    f"[System: You have approximately {remaining} tool calls remaining "
                    "before the hard limit. Continue working efficiently.]"
                )
                messages.append({"role": "user", "content": pre_warning})
                self.db.add_message(session_id, "user", pre_warning)

            # Emit narration before tool calls
            narrator = _narrate_tool_switch(
                [c.name for c in tool_calls],
                self.last_tool_names,
            )
            if narrator and self.thinking.should_show_summaries():
                self.activity.agent_status(message=narrator, session_id=session_id)

            # Sanitize content before storing in history
            if dsml_detected_this_turn:
                sanitized_content = ""
            else:
                sanitized_content = strip_tool_markup(response.content or "")
                if self._suppress_planning and self._is_intermediate_planning(sanitized_content):
                    sanitized_content = ""
                sanitized_content = self._final_sanitize(sanitized_content)

            messages.append(
                assistant_message_with_tool_calls(
                    sanitized_content or None, tool_calls, response.reasoning_content,
                )
            )
            assistant_id = self.db.add_message(
                session_id, "assistant", sanitized_content or "[tool call]",
                metadata={"tool_calls": [call.__dict__ for call in tool_calls]},
            )

            for call in tool_calls:
                total_tool_calls += 1
                self.last_tool_names.append(call.name)

                # Check total tool call budget
                if total_tool_calls >= self.config.agent.max_tool_calls:
                    msg = (
                        f"[System: Total tool call limit ({self.config.agent.max_tool_calls}) reached. "
                        "Provide a final answer now based on what you have so far. "
                        "Do not call any more tools.]"
                    )
                    messages.append({"role": "user", "content": msg})
                    self.db.add_message(session_id, "user", msg)
                    try:
                        response = self.provider.chat(messages, tools=None, tool_choice=None)
                    except ProviderError:
                        raise MaxIterationsReached()
                    content = plain_text(response.content)
                    content = self._final_sanitize(content)
                    self.db.add_message(session_id, "assistant", content)
                    self._after_turn(
                        session_id, user_input, content,
                        self.last_tool_names, improvement_context,
                    )
                    self.activity.task_completed(message=content[:200], session_id=session_id)
                    self.activity.flush()
                    return content

                # Tool started event
                self.activity.tool_started(
                    call.name, call.arguments,
                    session_id=session_id,
                )
                if self.state_callback:
                    self.state_callback(f"Running: {call.name}")

                # Checkpoint before mutation
                if self._is_mutating_tool(call.name):
                    raw = call.arguments.get("path", call.arguments.get("paths", []))
                    affected = [raw] if isinstance(raw, str) else list(raw)
                    if affected:
                        cp = self.checkpoints.create(
                            reason=f"Before {call.name}",
                            tool_name=call.name,
                            affected_paths=[str(p) for p in affected],
                            session_id=session_id,
                        )
                        if cp:
                            self.activity.checkpoint_created(
                                checkpoint_id=cp.id,
                                affected=affected,
                                session_id=session_id,
                            )

                # Execute tool through normal executor (permissions, etc.)
                start_time = time.monotonic()
                result = self._execute_tool(session_id, call)
                elapsed_ms = max(1, int((time.monotonic() - start_time) * 1000))

                # Tool completed/failed event
                if result.success:
                    self.activity.tool_completed(
                        call.name,
                        result_summary=result.content[:200] if result.content else "",
                        elapsed_ms=elapsed_ms,
                        session_id=session_id,
                    )
                    self.thinking.render_status("reviewing_results")
                else:
                    self.activity.tool_failed(
                        call.name,
                        error=result.content[:200],
                        elapsed_ms=elapsed_ms,
                        session_id=session_id,
                    )

                # Plan item update
                if self.plan_manager.is_active:
                    active = self.plan_manager.get_active_item()
                    if active:
                        if result.success:
                            self.plan_manager.complete_item(
                                active.id,
                                summary=result.content[:200],
                            )
                        else:
                            self.plan_manager.fail_item(
                                active.id,
                                error=result.content[:200],
                            )

                self.db.add_message(
                    session_id, "tool", result.content,
                    metadata={
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "assistant_id": assistant_id,
                    },
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result.content,
                })

            if self.state_callback:
                self.state_callback("Thinking")

        # Hard limit reached - report honestly what was and wasn't completed
        completed_tools = set(self.last_tool_names)
        summary_intro = "I reached the execution limit before completing this task."
        if completed_tools:
            summary_intro += f" Completed tools: {', '.join(sorted(completed_tools))}."
        summary_intro += " The task was not fully completed."

        final_turn = (
            f"[System: {summary_intro} "
            "Provide a final answer now based on what you have so far. "
            "Do not call any more tools.]"
        )
        messages.append({"role": "user", "content": final_turn})
        self.db.add_message(session_id, "user", final_turn)
        try:
            response = self.provider.chat(messages, tools=None, tool_choice=None)
        except ProviderError:
            raise MaxIterationsReached()
        content = plain_text(response.content)
        content = self._final_sanitize(content)
        self.db.add_message(session_id, "assistant", content)
        self._after_turn(
            session_id, user_input, content,
            self.last_tool_names, improvement_context,
        )
        self.activity.task_completed(message=content[:200], session_id=session_id)
        self.activity.flush()
        return content

    def _is_mutating_tool(self, tool_name: str) -> bool:
        mutating = {"write_file", "patch_file", "execute"}
        return tool_name in mutating

    def _after_turn(
        self, session_id: str, user_input: str, content: str,
        tool_names: list[str], improvement_context: Any,
    ) -> None:
        """Run post-turn hooks: memory, skills, scheduling."""
        self._turn_count += 1
        is_substantial = bool(tool_names) and len(user_input.split()) > 3

        if is_substantial or self._turn_count % 5 == 0:
            self.self_improvement.after_turn(
                session_id=session_id, user_input=user_input,
                assistant_output=content, tool_names=tool_names,
                context=improvement_context,
            )

        if is_substantial or self._turn_count % 5 == 0:
            skill_result = self.skill_learner.extract_candidate(
                user_input, content, tool_names, [],
            )
            if skill_result:
                draft = self.skill_learner.process_candidate(skill_result)
                if draft:
                    if getattr(draft, '_auto_saved', False):
                        self.activity.skill_learned(
                            name=draft.name,
                            session_id=session_id,
                        )
                    else:
                        self.activity.skill_draft_created(
                            name=draft.name,
                            session_id=session_id,
                        )

        if self._turn_count % 10 == 0 and self.config.memory.auto_summarize_sessions and self.config.memory.enabled:
            try:
                recent = self.db.recent_messages(session_id, limit=24)
                summary = generate_session_summary(self.db, session_id, recent)
                self.memory_manager.add_memory(
                    memory_type="session_summary",
                    title=f"Session {session_id[:8]}",
                    content=summary,
                    session_id=session_id,
                    source="agent_summary",
                )
            except Exception as exc:
                logger.debug("Failed to generate session summary: %s", exc)

    def _execute_tool(self, session_id: str, call: ToolCall) -> Any:
        if self.state_callback:
            self.state_callback(f"Working: {call.name}")
        if self.tool_callback:
            self.tool_callback(call.name, call.arguments)
        ctx = ToolExecutionContext(
            config=self.config,
            db=self.db,
            console=self.console,
            session_id=session_id,
            approval=self.approval,
            subagent_manager=self.subagent_manager,
            browser_service=self.browser_service,
            mcp_manager=self.mcp_manager,
        )
        result = self.registry.execute(ctx, call.name, call.arguments, tool_call_id=call.id)
        return result

    def stream_simple_turn(self, session_id: str, user_input: str) -> str:
        """Stream direct chat when no tools are enabled or desired."""
        self.db.add_message(session_id, "user", user_input)
        mcp_servers = [
            {"name": s.name, "type": s.type, "connected": s.connected}
            for s in self.mcp_manager.list_statuses()
        ]
        messages = build_messages(
            self.config, self.db, self.registry, session_id, user_input,
            recalled_memories=self._turn_recalled_memories,
            mcp_servers=mcp_servers,
        )
        chunks: list[str] = []
        try:
            for chunk in self.provider.stream_chat(messages, tools=None):
                chunks.append(chunk)
                self.activity.emit(
                    "agent_message_delta",
                    message=chunk,
                    session_id=session_id,
                )
                self.console.print(chunk, end="")
            self.console.print()
        except ProviderError:
            return self.run_turn(session_id, user_input)
        content = plain_text("".join(chunks))
        self.db.add_message(session_id, "assistant", content)
        self.activity.flush()
        return content

    def run_subagent_task(self, subagent_id: str, prompt: str) -> str:
        """Run a subagent task with the main provider."""
        from donovanagent.agent.prompts import SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        tool_schemas = self.registry.openai_schemas()
        try:
            response = self.provider.chat(messages, tools=tool_schemas if tool_schemas else None)
        except ProviderError:
            return "Subagent task failed due to provider error."
        return plain_text(response.content)

    def run_scheduled_task(self, task: Any) -> str:
        """Execute a scheduled task."""
        session_id = self.start_session()
        self.db.update_session_title(session_id, f"[Scheduled] {task.name}")
        self.activity.emit(
            "scheduler_task_started",
            message=f"Scheduled task: {task.name}",
            session_id=session_id,
        )
        try:
            result = self.run_turn(session_id, task.prompt)
            self.activity.emit(
                "scheduler_task_completed",
                message=f"Scheduled task completed: {task.name}",
                session_id=session_id,
            )
            return result
        except Exception as exc:
            self.activity.emit(
                "scheduler_task_failed",
                error=str(exc),
                session_id=session_id,
            )
            return f"Scheduled task failed: {exc}"

    def set_activity_renderer(self, renderer: Any) -> None:
        self.activity_renderer = renderer


def _narrate_tool_switch(current_tools: list[str], previous_tools: list[str]) -> str:
    if not previous_tools and current_tools:
        first = current_tools[0]
        narrations = {
            "web_search": "Let me search the web for that.",
            "read_file": "Let me read the relevant files.",
            "search_files": "Let me search the codebase.",
            "run_shell": "Let me run a command to check.",
            "list_directory": "Let me inspect the project structure.",
            "get_system_info": "Let me check the system information.",
            "browser_open": "Let me open that in the browser.",
            "browser_snapshot": "Let me read the page content.",
            "browser_screenshot": "Let me capture the page visually.",
            "browser_click": "Let me click that element.",
            "browser_type": "Let me type into that field.",
            "browser_extract_links": "Let me extract the links on this page.",
        }
        return narrations.get(first, f"Let me use {first}.")
    if previous_tools and current_tools:
        last_prev = previous_tools[-1] if previous_tools else ""
        first_curr = current_tools[0] if current_tools else ""
        if last_prev and first_curr and last_prev != first_curr:
            transitions = {
                ("web_search", "read_file"): "Now let me read what I found.",
                ("web_search", "run_shell"): "Let me run a command to follow up.",
                ("read_file", "patch_file"): "I see the issue. Let me fix it.",
                ("read_file", "write_file"): "I see what needs to change.",
                ("run_shell", "read_file"): "Let me check the output carefully.",
                ("search_files", "read_file"): "Let me open the matching file.",
                ("run_shell", "web_search"): "Let me look for more information.",
                ("patch_file", "run_shell"): "Let me verify the change works.",
                ("write_file", "run_shell"): "Now let me test the change.",
                ("browser_open", "browser_snapshot"): "The page loaded. Let me read the content.",
                ("browser_click", "browser_snapshot"): "Let me see what changed after clicking.",
                ("browser_type", "browser_snapshot"): "Let me verify the input was accepted.",
                ("browser_snapshot", "browser_click"): "Let me interact with the page.",
                ("browser_snapshot", "browser_extract_links"): "Let me see what links are available.",
            }
            key = (last_prev, first_curr)
            if key in transitions:
                return transitions[key]
    return ""


def estimate_tokens_from_messages(messages: list[dict[str, Any]]) -> int:
    total_chars = 0
    for message in messages:
        total_chars += len(str(message.get("content") or ""))
    return max(1, total_chars // 4)
