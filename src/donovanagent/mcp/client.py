"""MCP client for JSON-RPC communication with MCP servers."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from donovanagent.mcp.protocol import (
    McpError,
    make_initialize_params,
    make_list_tools_params,
    make_call_tool_params,
    make_list_resources_params,
    make_read_resource_params,
    make_list_prompts_params,
    make_get_prompt_params,
)
from donovanagent.mcp.transport import McpTransport, StdioMcpTransport
from donovanagent.mcp.transport_http import HttpMcpTransport, SseMcpTransport
from donovanagent.mcp.config import McpServerConfigModel, expand_env_vars
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class McpToolInfo:
    """Information about an MCP tool."""
    name: str
    description: str
    inputSchema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResourceInfo:
    """Information about an MCP resource."""
    uri: str
    name: str
    description: str = ""
    mimeType: str = ""


@dataclass
class McpResourceContent:
    """Content of an MCP resource."""
    uri: str
    mimeType: str = ""
    text: str = ""
    blob: str | None = None


@dataclass
class McpPromptInfo:
    """Information about an MCP prompt."""
    name: str
    description: str = ""
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class McpPromptResult:
    """Result of executing an MCP prompt."""
    description: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class McpServerCapabilities:
    """Capabilities discovered from an MCP server."""
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[McpResourceInfo] = field(default_factory=list)
    prompts: list[McpPromptInfo] = field(default_factory=list)
    protocol_version: str = ""
    server_name: str = ""
    server_version: str = ""
    supports_tools: bool = False
    supports_resources: bool = False
    supports_prompts: bool = False


class McpClient:
    """MCP client that communicates with an MCP server via its transport.

    Handles initialization, tool/resource/prompt discovery and execution.
    """

    def __init__(self, config: McpServerConfigModel) -> None:
        self.config = config
        self.transport: McpTransport = self._build_transport()
        self.capabilities: McpServerCapabilities = McpServerCapabilities()
        self._initialized = False
        self._last_connected: float = 0.0
        self._last_error: str = ""
        self._max_output_tokens = config.max_output_tokens

    def _build_transport(self) -> McpTransport:
        """Build the appropriate transport based on server config."""
        if self.config.type == "stdio":
            resolved_env = self.config.resolve_env()
            return StdioMcpTransport(
                command=self.config.resolved_command(),
                args=self.config.resolved_args(),
                env=resolved_env,
            )
        elif self.config.type == "http":
            resolved_headers = {
                k: expand_env_vars(v)
                for k, v in self.config.headers.items()
            }
            return HttpMcpTransport(
                url=self.config.url,
                headers=resolved_headers,
                timeout_ms=self.config.timeout_ms,
            )
        elif self.config.type == "sse":
            resolved_headers = {
                k: expand_env_vars(v)
                for k, v in self.config.headers.items()
            }
            return SseMcpTransport(
                url=self.config.url,
                headers=resolved_headers,
                timeout_ms=self.config.timeout_ms,
            )
        else:
            raise McpError(-32000, f"Unsupported MCP transport type: {self.config.type}")

    @property
    def is_connected(self) -> bool:
        return self._initialized and self.transport.is_connected

    @property
    def last_connected(self) -> float:
        return self._last_connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def connect(self) -> None:
        """Connect to the MCP server and perform initialization handshake."""
        try:
            self.transport.connect(timeout_ms=self.config.timeout_ms)

            # Send initialize request
            init_params = make_initialize_params()
            result = self.transport.send_request("initialize", init_params)

            # Parse capabilities
            caps = result.get("capabilities", {})
            server_info = result.get("serverInfo", {})
            protocol_version = result.get("protocolVersion", "")

            self.capabilities = McpServerCapabilities(
                protocol_version=protocol_version,
                server_name=server_info.get("name", ""),
                server_version=server_info.get("version", ""),
                supports_tools=isinstance(caps.get("tools"), dict),
                supports_resources=isinstance(caps.get("resources"), dict),
                supports_prompts=isinstance(caps.get("prompts"), dict),
            )

            # Send initialized notification
            self.transport.send_notification("notifications/initialized")

            self._initialized = True
            self._last_connected = time.time()
            self._last_error = ""
            logger.info(
                "MCP client initialized: %s %s (protocol %s)",
                self.capabilities.server_name,
                self.capabilities.server_version,
                self.capabilities.protocol_version,
            )
        except McpError:
            self._last_error = "Connection failed"
            raise
        except Exception as exc:
            self._last_error = str(exc)
            raise McpError(-32000, f"MCP initialization failed: {exc}") from exc

    def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        try:
            self.transport.disconnect()
        except Exception as exc:
            logger.warning("MCP disconnect error: %s", exc)
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise McpError(-32000, "MCP client is not initialized. Call connect() first.")

    def list_tools(self) -> list[McpToolInfo]:
        """Discover tools from the MCP server."""
        self._ensure_initialized()
        if not self.capabilities.supports_tools:
            return []

        tools: list[McpToolInfo] = []
        cursor: str | None = None

        while True:
            params = make_list_tools_params(cursor)
            result = self.transport.send_request("tools/list", params)
            for item in result.get("tools", []):
                tools.append(McpToolInfo(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    inputSchema=item.get("inputSchema", {}),
                    annotations=item.get("annotations", {}),
                ))
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self.capabilities.tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool on the MCP server."""
        self._ensure_initialized()
        params = make_call_tool_params(name, arguments)

        try:
            result = self.transport.send_request("tools/call", params)
        except McpError as exc:
            if exc.code == -32601:
                raise McpError(-32000, f"Tool '{name}' not found on MCP server") from exc
            raise

        # Apply output token limit
        content = result.get("content", [])
        truncated = self._truncate_content(content)
        if truncated != content:
            result["content"] = truncated
            result["_truncated"] = True

        return result

    def _truncate_content(self, content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Truncate text content to max_output_tokens limit."""
        total_chars = self._max_output_tokens * 4  # rough char-to-token estimate
        truncated = []
        chars = 0
        for item in content:
            if item.get("type") == "text":
                text = item.get("text", "")
                if chars + len(text) > total_chars:
                    allowed = total_chars - chars
                    if allowed > 0:
                        item["text"] = text[:allowed] + "\n[Output truncated...]"
                        truncated.append(item)
                    else:
                        truncated.append({"type": "text", "text": "[Output truncated...]"})
                    return truncated
                chars += len(text)
            truncated.append(item)
        return truncated

    def list_resources(self) -> list[McpResourceInfo]:
        """Discover resources from the MCP server."""
        self._ensure_initialized()
        if not self.capabilities.supports_resources:
            return []

        resources: list[McpResourceInfo] = []
        cursor: str | None = None

        while True:
            params = make_list_resources_params(cursor)
            result = self.transport.send_request("resources/list", params)
            for item in result.get("resources", []):
                resources.append(McpResourceInfo(
                    uri=item.get("uri", ""),
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    mimeType=item.get("mimeType", ""),
                ))
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self.capabilities.resources = resources
        return resources

    def read_resource(self, uri: str) -> McpResourceContent | None:
        """Read a specific resource from the MCP server."""
        self._ensure_initialized()
        params = make_read_resource_params(uri)

        try:
            result = self.transport.send_request("resources/read", params)
        except McpError as exc:
            if exc.code in (-32601, -32002):
                return None
            raise

        contents = result.get("contents", [])
        if not contents:
            return None

        first = contents[0]
        return McpResourceContent(
            uri=first.get("uri", uri),
            mimeType=first.get("mimeType", ""),
            text=first.get("text", ""),
            blob=first.get("blob"),
        )

    def list_prompts(self) -> list[McpPromptInfo]:
        """Discover prompts from the MCP server."""
        self._ensure_initialized()
        if not self.capabilities.supports_prompts:
            return []

        prompts: list[McpPromptInfo] = []
        cursor: str | None = None

        while True:
            params = make_list_prompts_params(cursor)
            result = self.transport.send_request("prompts/list", params)
            for item in result.get("prompts", []):
                prompts.append(McpPromptInfo(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    arguments=item.get("arguments", []),
                ))
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self.capabilities.prompts = prompts
        return prompts

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> McpPromptResult:
        """Execute an MCP prompt."""
        self._ensure_initialized()
        params = make_get_prompt_params(name, arguments)

        try:
            result = self.transport.send_request("prompts/get", params)
        except McpError as exc:
            if exc.code == -32601:
                raise McpError(-32000, f"Prompt '{name}' not found on MCP server") from exc
            raise

        return McpPromptResult(
            description=result.get("description", ""),
            messages=result.get("messages", []),
        )
