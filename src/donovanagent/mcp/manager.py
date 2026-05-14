"""McpManager — central orchestrator for MCP integration.

Manages server lifecycle, capability discovery, dynamic tool registration,
resource/prompt exposure, and security enforcement.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from donovanagent.config.paths import DonovanAgentPaths, get_paths
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.mcp.client import McpClient, McpToolInfo, McpResourceInfo, McpPromptInfo
from donovanagent.mcp.config import (
    McpConfigStore,
    McpServerConfigModel,
    ConfigScope,
    TrustLevel,
    mask_secret,
    is_secret_var,
)
from donovanagent.mcp.registry import (
    McpToolRegistry,
    McpResourceRegistry,
    McpPromptRegistry,
    _mcp_tool_name,
)
from donovanagent.mcp.search import McpToolSearch
from donovanagent.mcp.security import McpRiskClassifier, McpTrustStore
from donovanagent.mcp.protocol import McpError
from donovanagent.tools.base import ToolDefinition, ToolResult, ToolExecutionContext
from donovanagent.tools.registry import ToolRegistry
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class McpServerStatus:
    """Status of a single MCP server connection."""
    name: str
    type: str
    scope: str
    enabled: bool
    trust: str
    connected: bool
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    last_connected: float = 0.0
    last_error: str = ""
    stderr_log: list[str] = field(default_factory=list)


class McpManager:
    """Central orchestrator for all MCP functionality.

    Responsibilities:
    - Load MCP config from all scopes
    - Start/stop MCP servers
    - Track connection status
    - Initialize MCP clients
    - Discover tools/resources/prompts
    - Register MCP tools with Donovan's tool system
    - Handle server restarts and failures
    - Enforce trust and permission checks
    """

    def __init__(
        self,
        config: DonovanAgentConfig,
        donovan_registry: ToolRegistry,
        paths: DonovanAgentPaths | None = None,
        project_dir: str | None = None,
    ) -> None:
        self.config = config
        self.donovan_registry = donovan_registry
        self.paths = paths or get_paths()
        self.project_dir = str(Path(project_dir).resolve()) if project_dir else str(Path.cwd().resolve())

        self.config_store = McpConfigStore(self.paths, self.project_dir)
        self.trust_store = McpTrustStore(self.paths, self.project_dir)

        self.tool_registry = McpToolRegistry()
        self.resource_registry = McpResourceRegistry()
        self.prompt_registry = McpPromptRegistry()
        self.tool_search = McpToolSearch()

        self._clients: dict[str, McpClient] = {}  # server_name -> client
        self._client_lock = threading.Lock()
        self._status_cache: list[McpServerStatus] = []
        self._initialized_servers: set[str] = set()

        # Callbacks for Donovan integration
        self.on_tools_changed: Callable[[], None] | None = None
        self.on_prompts_changed: Callable[[], None] | None = None

    @property
    def configured_servers(self) -> list[tuple[str, McpServerConfigModel, ConfigScope]]:
        """Return all configured servers (name, config, scope)."""
        return [
            (name, config, scope)
            for name, (config, scope) in self.config_store.load_all().items()
        ]

    @property
    def connected_servers(self) -> list[str]:
        """Return names of currently connected servers."""
        return list(self._clients.keys())

    def should_defer_tools(self) -> bool:
        """Check if MCP tools should be deferred (not all injected)."""
        return self.tool_registry.total_count() > 30  # configurable default

    def get_server_status(self, name: str) -> McpServerStatus | None:
        """Get detailed status for a specific server."""
        config_model, scope = self.config_store.load_server(name)
        if config_model is None:
            return None

        client = self._clients.get(name)
        is_connected = client is not None and client.is_connected
        tool_count = len(self.tool_registry.get_server_tools(name))
        resource_count = len(self.resource_registry.get_server_resources(name))
        prompt_count = len(self.prompt_registry.get_server_prompts(name))

        last_error = ""
        stderr_log: list[str] = []
        last_connected: float = 0.0
        if client:
            last_error = client.last_error
            stderr_log = client.transport.stderr_log
            last_connected = client.last_connected

        return McpServerStatus(
            name=name,
            type=config_model.type,
            scope=scope,
            enabled=config_model.enabled,
            trust=config_model.trust,
            connected=is_connected,
            tool_count=tool_count,
            resource_count=resource_count,
            prompt_count=prompt_count,
            last_connected=last_connected,
            last_error=last_error,
            stderr_log=stderr_log,
        )

    def list_statuses(self) -> list[McpServerStatus]:
        """List status of all configured servers."""
        statuses: list[McpServerStatus] = []
        for name, config_model, scope in self.configured_servers:
            statuses.append(self.get_server_status(name) or McpServerStatus(
                name=name,
                type=config_model.type,
                scope=scope,
                enabled=config_model.enabled,
                trust=config_model.trust,
                connected=False,
            ))
        return statuses

    def connect_server(self, name: str, force: bool = False) -> str:
        """Connect to an MCP server.

        Handles trust checks, transport creation, initialization,
        and capability discovery.

        Returns a status message describing the result.
        """
        config_model, scope = self.config_store.load_server(name)
        if config_model is None:
            return f"MCP server '{name}' not found in configuration."

        if not config_model.enabled:
            return f"MCP server '{name}' is disabled. Use 'donovan mcp enable {name}' to enable."

        # Check if blocked
        if self.trust_store.is_blocked(name, config_model, scope):
            return f"MCP server '{name}' is blocked. Use 'donovan mcp trust {name}' to unblock."

        # Check if config changed since last trust decision
        if self.trust_store.has_config_changed(name, config_model, scope):
            return (
                f"MCP server '{name}' configuration has changed since it was last trusted. "
                f"Please review and trust it again."
            )

        # Check if already connected
        if name in self._clients and self._clients[name].is_connected:
            if not force:
                return f"MCP server '{name}' is already connected."
            self.disconnect_server(name)

        # Create and connect client
        try:
            client = McpClient(config_model)
            client.connect()
        except McpError as exc:
            logger.warning("MCP connection failed for '%s': %s", name, exc)
            return str(exc)
        except Exception as exc:
            logger.error("MCP unexpected error for '%s': %s", name, exc)
            return f"Unexpected error connecting to '{name}': {exc}"

        # Discover capabilities
        errors: list[str] = []
        tools: list[McpToolInfo] = []
        resources: list[McpResourceInfo] = []
        prompts: list[McpPromptInfo] = []

        try:
            tools = client.list_tools()
        except McpError as exc:
            errors.append(f"tools: {exc}")

        try:
            resources = client.list_resources()
        except McpError as exc:
            errors.append(f"resources: {exc}")

        try:
            prompts = client.list_prompts()
        except McpError as exc:
            errors.append(f"prompts: {exc}")

        # Register capabilities
        with self._client_lock:
            self._clients[name] = client

            self.tool_registry.unregister_server(name)
            self.tool_registry.register_server_tools(name, tools)

            self.resource_registry.unregister_server(name)
            self.resource_registry.register_server_resources(name, resources)

            self.prompt_registry.unregister_server(name)
            self.prompt_registry.register_server_prompts(name, prompts)

            self._initialized_servers.add(name)

        # Rebuild tool search index
        self.tool_search.rebuild(self.tool_registry)

        # Register tools with Donovan's tool system
        self._register_donovan_tools()

        # Notify prompt changes
        if self.on_prompts_changed:
            self.on_prompts_changed()

        # Build status message
        parts = [f"Connected to MCP server '{name}' ({config_model.type})"]
        if tools:
            parts.append(f"  Tools: {len(tools)}")
        if resources:
            parts.append(f"  Resources: {len(resources)}")
        if prompts:
            parts.append(f"  Prompts: {len(prompts)}")
        if errors:
            parts.append(f"  Warnings: {'; '.join(errors)}")

        return "\n".join(parts)

    def disconnect_server(self, name: str) -> str:
        """Disconnect from an MCP server and unregister its capabilities."""
        client = self._clients.pop(name, None)
        if client is None:
            return f"MCP server '{name}' is not connected."

        try:
            client.disconnect()
        except Exception as exc:
            logger.warning("Error disconnecting MCP server '%s': %s", name, exc)

        # Unregister capabilities
        self.tool_registry.unregister_server(name)
        self.resource_registry.unregister_server(name)
        self.prompt_registry.unregister_server(name)
        self._initialized_servers.discard(name)

        # Rebuild search index and Donovan tools
        self.tool_search.rebuild(self.tool_registry)
        self._register_donovan_tools()

        return f"Disconnected from MCP server '{name}'."

    def restart_server(self, name: str) -> str:
        """Restart an MCP server connection."""
        self.disconnect_server(name)
        # Small delay to allow process cleanup
        time.sleep(0.5)
        return self.connect_server(name)

    def connect_all(self) -> list[str]:
        """Connect to all enabled and trusted servers."""
        messages: list[str] = []
        for name, config_model, scope in self.configured_servers:
            if not config_model.enabled:
                continue
            if self.trust_store.is_blocked(name, config_model, scope):
                continue
            if name in self._clients:
                continue
            msg = self.connect_server(name)
            messages.append(msg)
        return messages

    def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        for name in list(self._clients.keys()):
            self.disconnect_server(name)

    def _register_donovan_tools(self) -> None:
        """Register/unregister dynamic MCP tools with Donovan's ToolRegistry.

        Called after every connect/disconnect to keep the tool list in sync.
        Dynamic MCP tools use the naming convention ``mcp__<server>__<tool>``.
        """
        # Unregister ALL existing dynamic MCP tools first
        existing = list(self.donovan_registry.list())
        for tool in existing:
            if tool.name.startswith("mcp__"):
                self.donovan_registry.unregister(tool.name)

        # Build and register current definitions
        def _client_getter(server_name: str) -> McpClient | None:
            return self._clients.get(server_name)

        definitions = self.tool_registry.to_donovan_definitions(_client_getter)
        for defn in definitions:
            self.donovan_registry.register(defn)

        self._mcp_definitions = definitions

    @property
    def mcp_tool_definitions(self) -> list[ToolDefinition]:
        """Get current MCP tool definitions (read-only)."""
        return list(getattr(self, "_mcp_definitions", []))

    def get_client(self, server_name: str) -> McpClient | None:
        """Get the MCP client for a connected server."""
        return self._clients.get(server_name)

    def read_resource(self, server_name: str, uri: str) -> str | None:
        """Read a resource from an MCP server. Returns text content or None."""
        client = self._clients.get(server_name)
        if client is None or not client.is_connected:
            return None

        try:
            content = client.read_resource(uri)
            if content is None:
                return None
            return content.text or content.blob or ""
        except Exception as exc:
            logger.warning("Failed to read MCP resource %s: %s", uri, exc)
            return None

    def get_prompt_messages(self, server_name: str, prompt_name: str,
                           arguments: dict[str, Any] | None = None) -> list[dict[str, Any]] | None:
        """Execute an MCP prompt and return the messages."""
        client = self._clients.get(server_name)
        if client is None or not client.is_connected:
            return None

        try:
            result = client.get_prompt(prompt_name, arguments)
            return result.messages
        except Exception as exc:
            logger.warning("Failed to execute MCP prompt %s: %s", prompt_name, exc)
            return None

    def cleanup(self) -> None:
        """Disconnect all servers and clean up resources."""
        self.disconnect_all()
