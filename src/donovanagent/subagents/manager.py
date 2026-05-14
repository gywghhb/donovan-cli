from __future__ import annotations

import concurrent.futures
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from donovanagent.subagents.models import Subagent, SubagentRole
from donovanagent.subagents.roles import ROLE_PRESETS
from donovanagent.utils.logging import get_logger
from donovanagent.utils.web_search import is_web_search_enabled

logger = get_logger(__name__)

# Maximum subagents that can run simultaneously
_MAX_PARALLEL = 3


class SubagentManager:
    """Manages isolated subagents for parallel work.

    Each subagent runs as a single LLM chat call with a role-specific
    system prompt and restricted tool set.  Results are collected and
    reported back through the manager.

    To extend: subclass or monkey-patch ``_run_subagent`` to implement
    a different execution strategy (e.g. a full tool loop).
    """

    def __init__(
        self,
        config: Any,
        *,
        provider_factory: Callable[[], Any] | None = None,
        tool_schema_filter: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    ) -> None:
        """Initialize the manager.

        Parameters
        ----------
        config:
            DonovanAgent configuration object.
        provider_factory:
            Callable that returns a new LLM provider instance for each
            subagent.  If omitted the subagent will record a descriptive
            result without making an LLM call (useful for testing).
        tool_schema_filter:
            Callable that accepts a list of tool names and returns the
            corresponding OpenAI-compatible tool schemas.  If omitted
            subagents won't receive tool schemas.
        """
        self.config = config
        self._provider_factory = provider_factory
        self._tool_schema_filter = tool_schema_filter
        self._subagents: dict[str, Subagent] = {}
        self._running: set[str] = set()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_PARALLEL
        )
        self._futures: dict[str, concurrent.futures.Future] = {}

    # ------------------------------------------------------------------
    # Subagent lifecycle
    # ------------------------------------------------------------------

    def create(
        self,
        role: str | SubagentRole,
        goal: str,
        *,
        name: str | None = None,
        allowed_tools: list[str] | None = None,
        permissions: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        workspace: str | None = None,
    ) -> Subagent:
        """Create a new subagent with the given role and goal.

        Returns the Subagent object in ``pending`` status.  Call
        ``start()`` or ``create_and_start()`` to begin execution.
        """
        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        role_key = role.value if isinstance(role, SubagentRole) else role
        preset = ROLE_PRESETS.get(role_key, {})

        system_prompt = preset.get("description", "")
        full_prompt = f"{system_prompt}\n\nGoal: {goal}" if system_prompt else goal

        subagent = Subagent(
            id=sub_id,
            name=name or preset.get("name", str(role)),
            role=SubagentRole(role) if isinstance(role, str) else role,
            goal=goal,
            prompt=full_prompt,
            allowed_tools=allowed_tools or preset.get("tools", []),
            permissions=permissions or preset.get("permissions", "read_only"),
            provider=provider,
            model=model,
            workspace=workspace,
        )

        # Filter out web_search if the API key is not configured
        ws_enabled = is_web_search_enabled(self.config)
        subagent.web_search_enabled = ws_enabled
        if "web_search" in subagent.allowed_tools and not ws_enabled:
            subagent.allowed_tools = [t for t in subagent.allowed_tools if t != "web_search"]

        self._subagents[sub_id] = subagent
        return subagent

    def create_and_start(
        self,
        role: str | SubagentRole,
        goal: str,
        **kwargs: Any,
    ) -> Subagent:
        """Create a subagent and immediately start it in a background thread."""
        sub = self.create(role, goal, **kwargs)
        sub.status = "running"
        sub.started_at = datetime.now(timezone.utc).isoformat()
        self._running.add(sub.id)
        future = self._executor.submit(self._run_subagent, sub.id)
        self._futures[sub.id] = future
        return sub

    def start(self, subagent_id: str) -> None:
        """Set a subagent's status to running (without spawning a thread).

        Use ``create_and_start()`` to also submit to the thread pool.
        """
        sub = self._subagents.get(subagent_id)
        if not sub:
            return
        sub.status = "running"
        sub.started_at = datetime.now(timezone.utc).isoformat()
        self._running.add(subagent_id)

    def complete(self, subagent_id: str, result: str) -> None:
        """Mark a subagent as completed with a result summary."""
        sub = self._subagents.get(subagent_id)
        if sub:
            sub.status = "completed"
            sub.result_summary = result
            sub.completed_at = datetime.now(timezone.utc).isoformat()
            self._running.discard(subagent_id)
            logger.info("Subagent %s completed", subagent_id)

    def fail(self, subagent_id: str, error: str) -> None:
        """Mark a subagent as failed."""
        sub = self._subagents.get(subagent_id)
        if sub:
            sub.status = "failed"
            sub.error = error
            sub.completed_at = datetime.now(timezone.utc).isoformat()
            self._running.discard(subagent_id)
            logger.warning("Subagent %s failed: %s", subagent_id, error[:200])

    def kill(self, subagent_id: str) -> bool:
        """Terminate a running subagent.

        Returns True if the subagent was found and cancelled.
        """
        sub = self._subagents.get(subagent_id)
        if not sub:
            return False
        if subagent_id in self._futures:
            # Cancel the future (thread continues but result is discarded)
            self._futures[subagent_id].cancel()
            del self._futures[subagent_id]
        if sub.status != "completed":
            sub.status = "failed"
            sub.error = "Killed by user"
            sub.completed_at = datetime.now(timezone.utc).isoformat()
        self._running.discard(subagent_id)
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, subagent_id: str) -> Subagent | None:
        return self._subagents.get(subagent_id)

    def list(self) -> list[Subagent]:
        return list(self._subagents.values())

    def list_active(self) -> list[Subagent]:
        return [s for s in self._subagents.values() if s.status == "running"]

    @property
    def running_count(self) -> int:
        return len(self._running)

    @property
    def can_spawn(self) -> bool:
        cfg = self.config.subagents if hasattr(self.config, "subagents") else None
        max_parallel = getattr(cfg, "max_parallel", _MAX_PARALLEL)
        return self.running_count < max_parallel

    def clear(self) -> None:
        for sid in list(self._futures):
            self.kill(sid)
        self._subagents.clear()
        self._running.clear()

    def get_result_summary(self, subagent_id: str) -> str | None:
        sub = self._subagents.get(subagent_id)
        if not sub:
            return None
        if sub.status == "completed":
            return sub.result_summary
        return sub.error or sub.status

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run_subagent(self, subagent_id: str) -> None:
        """Execute a subagent's task against the LLM.

        This method is called from a background thread.  Override in a
        subclass to change the execution strategy.
        """
        sub = self._subagents.get(subagent_id)
        if not sub:
            return

        try:
            # --- build the conversation -----------------------------------
            messages = self._build_messages(sub)

            # --- get or skip provider -------------------------------------
            provider = self._provider_factory() if self._provider_factory else None
            if provider is None:
                # No provider available Ã¢â‚¬â€ just record the prompt as result
                self.complete(
                    subagent_id,
                    f"[Subagent {sub.name}]\nRole: {sub.role.value}\n"
                    f"Goal: {sub.goal}\n\nPrompt would be:\n{sub.prompt[:500]}",
                )
                return

            # --- select tool schemas --------------------------------------
            tools = None
            if self._tool_schema_filter and sub.allowed_tools:
                tools = self._tool_schema_filter(sub.allowed_tools)

            # --- make the LLM call ----------------------------------------
            response = provider.chat(messages, tools=tools or None)

            result = response.content or "(no response)"
            self.complete(subagent_id, result)

        except Exception as exc:
            logger.exception("Subagent %s threw an exception", subagent_id)
            self.fail(subagent_id, f"{type(exc).__name__}: {exc}")

    def _build_messages(self, sub: Subagent) -> list[dict[str, Any]]:
        """Build the message list for a subagent LLM call."""
        messages: list[dict[str, Any]] = []
        if sub.prompt:
            messages.append({"role": "system", "content": sub.prompt})
        # The user message conveys the goal
        messages.append({"role": "user", "content": sub.goal})
        return messages
