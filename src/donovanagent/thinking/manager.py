from __future__ import annotations

from typing import Any


class ThinkingManager:
    """Manages the display of model thinking/reasoning in the UI.

    Provides safe summaries during model thinking phases and optionally
    displays provider reasoning content when available and configured.
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self._thinking_enabled: bool = True
        self._safe_summaries: bool = True
        self._provider_reasoning: bool = False

    def configure(self, config: Any) -> None:
        if hasattr(config, "thinking"):
            self._thinking_enabled = bool(config.thinking.enabled)
            self._safe_summaries = bool(config.thinking.show_safe_summaries)
            self._provider_reasoning = bool(config.thinking.show_provider_reasoning_if_available)

    @property
    def enabled(self) -> bool:
        return self._thinking_enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._thinking_enabled = value

    def should_show_summaries(self) -> bool:
        return self._thinking_enabled and self._safe_summaries

    def should_show_provider_reasoning(self) -> bool:
        return self._thinking_enabled and self._provider_reasoning

    def get_summary(self, phase: str) -> str:
        """Get a safe thinking summary for the given phase."""
        if not self._thinking_enabled or not self._safe_summaries:
            return ""

        summaries = {
            "planning": "Thinking through the task...",
            "analyzing": "Analyzing the request...",
            "searching": "Deciding which tools to use...",
            "choosing_tool": "Selecting the best approach...",
            "reviewing_results": "Reviewing results...",
            "formulating": "Formulating the response...",
            "verifying": "Verifying the solution...",
        }
        return summaries.get(phase, "Thinking...")

    def render_status(self, phase: str) -> str | None:
        """Return a status message if thinking is enabled, otherwise None."""
        if self._thinking_enabled:
            return self.get_summary(phase)
        return None
