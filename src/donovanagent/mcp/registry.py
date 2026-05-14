"""MCP registries for tools, resources, and prompts.

Manages discovered MCP capabilities and integrates MCP tools
with Donovan's existing tool system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from donovanagent.mcp.client import (
    McpClient,
    McpToolInfo,
    McpResourceInfo,
    McpPromptInfo,
)
from donovanagent.mcp.security import McpRiskClassifier
from donovanagent.tools.base import ToolDefinition, ToolResult, ToolExecutionContext
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


def _mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Create a globally unique tool name for MCP tools.

    Naming convention: mcp__<serverName>__<toolName>
    """
    safe_server = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name)
    safe_tool = re.sub(r"[^a-zA-Z0-9_-]", "_", tool_name)
    return f"mcp__{safe_server}__{safe_tool}"


def _parse_mcp_tool_name(full_name: str) -> tuple[str, str] | None:
    """Parse a full MCP tool name back into (server_name, tool_name).

    Returns None if the name doesn't match the MCP convention.
    """
    if not full_name.startswith("mcp__"):
        return None
    parts = full_name.split("__", 2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


def repair_mcp_tool_name(malformed: str, registered_names: set[str]) -> str | None:
    """Attempt to repair a malformed MCP tool name.

    Handles these malformations:
    - ``mcpframerupdateXmlForNode`` (all joined, no separators)
    - ``mcp_framer_updateXmlForNode`` (single underscores)
    - ``mcp-framer-updateXmlForNode`` (hyphens)
    - ``mcp.framer.updateXmlForNode`` (dots)
    - ``mcpframer_getProjectXml`` (mixed joined + separator)

    Strategy: extract known server names from registered tools, then
    try matching the body (after stripping ``mcp``) against each server.
    Falls back to separator splitting for non-camelCase cases.

    Only returns a repair if it matches exactly one registered tool.
    """
    if not malformed.startswith("mcp"):
        return None

    # Direct check first
    if malformed in registered_names:
        return malformed

    # Extract known server names from registered MCP tools
    servers: set[str] = set()
    for name in registered_names:
        if name.startswith("mcp__"):
            parts = name.split("__", 2)
            if len(parts) >= 3:
                servers.add(parts[1])

    # Strip "mcp" prefix and any leading separator characters
    body = malformed[3:]
    body = body.lstrip("_.-")

    # Strategy 1: match body against known server names (handles all-joined cases)
    # Sort by length descending so "framerx" matches before "framer"
    for server in sorted(servers, key=len, reverse=True):
        if body == server:
            continue  # body IS the server — no tool part to work with
        if body.startswith(server):
            tool_part = body[len(server):]
            tool_part = tool_part.lstrip("_.-")
            if tool_part:
                candidate = f"mcp__{server}__{tool_part}"
                if candidate in registered_names:
                    logger.info(
                        "Repaired malformed MCP tool name %s -> %s", malformed, candidate
                    )
                    return candidate

    # Strategy 2: try splitting on common separators (underscore, hyphen, dot)
    parts = re.split(r"[_.-]+", body)
    if len(parts) >= 2:
        candidate = f"mcp__{parts[0]}__" + "__".join(parts[1:])
        if candidate in registered_names:
            logger.info(
                "Repaired malformed MCP tool name %s -> %s", malformed, candidate
            )
            return candidate

    return None


class McpToolRegistry:
    """Registry for MCP tools discovered from connected servers.

    Each MCP tool is wrapped as a Donovan ToolDefinition and can be
    registered with the main ToolRegistry.
    """

    def __init__(self) -> None:
        self._tools: dict[str, list[McpToolInfo]] = {}  # server_name -> tools
        self._tool_map: dict[str, str] = {}  # full_name -> server_name

    def register_server_tools(self, server_name: str, tools: list[McpToolInfo]) -> None:
        """Register tools from an MCP server."""
        self._tools[server_name] = tools
        names: list[str] = []
        for tool in tools:
            full_name = _mcp_tool_name(server_name, tool.name)
            self._tool_map[full_name] = server_name
            names.append(full_name)
        logger.info("Registered MCP tools for %s:\n%s", server_name, "\n".join(f"- {n}" for n in names))

    def unregister_server(self, server_name: str) -> None:
        """Remove all tools for a disconnected server."""
        if server_name in self._tools:
            for tool in self._tools[server_name]:
                full_name = _mcp_tool_name(server_name, tool.name)
                self._tool_map.pop(full_name, None)
            del self._tools[server_name]

    def get_all_tool_infos(self) -> list[tuple[str, McpToolInfo]]:
        """Return all registered tools as (full_name, info) pairs."""
        result: list[tuple[str, McpToolInfo]] = []
        for server_name, tools in self._tools.items():
            for tool in tools:
                result.append((_mcp_tool_name(server_name, tool.name), tool))
        return result

    def get_server_tools(self, server_name: str) -> list[McpToolInfo]:
        """Get all tools for a specific server."""
        return list(self._tools.get(server_name, []))

    def get_tool(self, full_name: str) -> McpToolInfo | None:
        """Get a specific MCP tool info by its full name."""
        parsed = _parse_mcp_tool_name(full_name)
        if not parsed:
            return None
        server_name, tool_name = parsed
        tools = self._tools.get(server_name, [])
        for t in tools:
            if t.name == tool_name:
                return t
        return None

    def total_count(self) -> int:
        return len(self._tool_map)

    def to_donovan_definitions(
        self, client_getter: Callable[[str], McpClient | None]
    ) -> list[ToolDefinition]:
        """Convert all registered MCP tools to Donovan ToolDefinitions."""
        definitions: list[ToolDefinition] = []
        for full_name, info in self.get_all_tool_infos():
            parsed = _parse_mcp_tool_name(full_name)
            if not parsed:
                continue
            server_name, tool_name = parsed

            risk_label, readable_risk, reasons = McpRiskClassifier.classify(
                server_name=server_name,
                tool_name=tool_name,
                description=info.description,
                input_schema=info.inputSchema,
                annotations=info.annotations,
            )

            def _make_handler(
                srv_name: str = server_name,
                t_name: str = tool_name,
                cb: Callable[[str], McpClient | None] = client_getter,
            ) -> Callable[[ToolExecutionContext, dict[str, Any]], ToolResult]:
                def handler(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
                    client = cb(srv_name)
                    if client is None:
                        return ToolResult(
                            False,
                            f"MCP server '{srv_name}' is not available. "
                            f"Use /mcp connect {srv_name} to connect.",
                        )
                    if not client.is_connected:
                        return ToolResult(
                            False,
                            f"MCP server '{srv_name}' is not connected. "
                            f"Use /mcp connect {srv_name} to connect.",
                        )
                    try:
                        result = client.call_tool(t_name, args)
                        content = result.get("content", [])
                        truncated = result.get("_truncated", False)

                        # Combine text content
                        text_parts: list[str] = []
                        for item in content:
                            if item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                            elif item.get("type") == "resource":
                                text_parts.append(f"[Resource: {item.get('resource', {}).get('uri', '')}]")

                        output = "\n".join(text_parts)
                        if truncated:
                            output += "\n[Output truncated due to size limits]"

                        return ToolResult(True, output, data=result)
                    except Exception as exc:
                        return ToolResult(False, f"MCP tool error: {exc}")

                return handler

            requires_approval = McpRiskClassifier.requires_approval(readable_risk, tool_name)

            params = info.inputSchema or {"type": "object", "properties": {}}
            if "required" not in params:
                params["required"] = []

            description = info.description or f"MCP tool from {server_name}"
            if readable_risk != "read-only":
                description += f" [Risk: {readable_risk}]"

            definitions.append(ToolDefinition(
                name=full_name,
                description=description,
                parameters=params,
                handler=_make_handler(),
                enabled_key="mcp_tools.enabled",
                requires_approval=requires_approval,
                risk="high" if readable_risk in ("destructive", "shell/command") else
                     "medium" if readable_risk in ("write", "network") else "low",
            ))
        return definitions


class McpResourceRegistry:
    """Registry for MCP resources from connected servers."""

    def __init__(self) -> None:
        self._resources: dict[str, list[McpResourceInfo]] = {}  # server_name -> resources

    def register_server_resources(self, server_name: str, resources: list[McpResourceInfo]) -> None:
        self._resources[server_name] = resources

    def unregister_server(self, server_name: str) -> None:
        self._resources.pop(server_name, None)

    def get_all(self) -> list[tuple[str, McpResourceInfo]]:
        result: list[tuple[str, McpResourceInfo]] = []
        for server_name, resources in self._resources.items():
            for resource in resources:
                result.append((server_name, resource))
        return result

    def get_server_resources(self, server_name: str) -> list[McpResourceInfo]:
        return list(self._resources.get(server_name, []))

    def find_by_uri(self, uri: str) -> tuple[str, McpResourceInfo] | None:
        """Find a resource by URI across all servers."""
        for server_name, resources in self._resources.items():
            for r in resources:
                if r.uri == uri:
                    return server_name, r
        return None


class McpPromptRegistry:
    """Registry for MCP prompts from connected servers."""

    def __init__(self) -> None:
        self._prompts: dict[str, list[McpPromptInfo]] = {}  # server_name -> prompts

    def register_server_prompts(self, server_name: str, prompts: list[McpPromptInfo]) -> None:
        self._prompts[server_name] = prompts

    def unregister_server(self, server_name: str) -> None:
        self._prompts.pop(server_name, None)

    def get_all(self) -> list[tuple[str, McpPromptInfo]]:
        result: list[tuple[str, McpPromptInfo]] = []
        for server_name, prompts in self._prompts.items():
            for prompt in prompts:
                result.append((server_name, prompt))
        return result

    def get_server_prompts(self, server_name: str) -> list[McpPromptInfo]:
        return list(self._prompts.get(server_name, []))

    @staticmethod
    def slash_command_name(server_name: str, prompt_name: str) -> str:
        """Create a slash command name for an MCP prompt."""
        safe_server = re.sub(r"[^a-zA-Z0-9_-]", "_", server_name)
        safe_prompt = re.sub(r"[^a-zA-Z0-9_-]", "_", prompt_name)
        return f"/mcp__{safe_server}__{safe_prompt}"
