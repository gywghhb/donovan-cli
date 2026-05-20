from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

from rich.console import Console

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.memory.database import MemoryDatabase

if TYPE_CHECKING:
    from donovanagent.browser.companion import BrowserCompanionService
    from donovanagent.browser.service import BrowserService
    from donovanagent.mcp.manager import McpManager
    from donovanagent.subagents.manager import SubagentManager

RiskLevel = Literal["low", "medium", "high"]


@dataclass
class ToolResult:
    success: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "data": self.data,
            "exit_code": self.exit_code,
        }


@dataclass
class ToolExecutionContext:
    config: DonovanAgentConfig
    db: MemoryDatabase
    console: Console
    session_id: str | None
    approval: "ApprovalManager"
    subagent_manager: SubagentManager | None = None
    browser_service: "BrowserService | None" = None
    browser_companion: "BrowserCompanionService | None" = None
    mcp_manager: "McpManager | None" = None


ToolHandler = Callable[[ToolExecutionContext, dict[str, Any]], ToolResult]


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    enabled_key: str
    requires_approval: bool = False
    risk: RiskLevel = "low"

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


from donovanagent.tools.approval import ApprovalManager  # noqa: E402
