from __future__ import annotations


class DonovanAgentError(Exception):
    """Base application error."""


class ConfigError(DonovanAgentError):
    """Configuration could not be loaded or validated."""


class ProviderError(DonovanAgentError):
    """LLM provider request failed."""


class ToolError(DonovanAgentError):
    """A tool failed before it could return a structured result."""


class PermissionDenied(DonovanAgentError):
    """The requested operation violates DonovanAgent permissions."""


class ApprovalDenied(DonovanAgentError):
    """The user did not approve a requested operation."""


class MaxIterationsReached(DonovanAgentError):
    """Agent hit the tool-iteration cap without producing a final answer."""
