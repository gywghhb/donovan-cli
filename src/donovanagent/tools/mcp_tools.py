"""Internal MCP control tools, DSML parser, and tool-name repair for Donovan.

This module provides MCP server management tools (registered as normal
ToolDefinitions), the DSML/internal tool-call parser that intercepts
markup from model output, and tool-name repair for extracted calls.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from donovanagent.tools.base import ToolDefinition, ToolExecutionContext, ToolResult


def _require_manager(ctx: ToolExecutionContext) -> Any:
    """Return the MCP manager or raise a useful error."""
    if ctx.mcp_manager is None:
        raise RuntimeError("MCP manager is not available in this context.")
    return ctx.mcp_manager


def _handle_list_servers(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """List configured MCP servers with status, trust, and capability counts."""
    manager = _require_manager(ctx)
    statuses = manager.list_statuses()
    if not statuses:
        return ToolResult(True, "No MCP servers configured.\n"
                                "Run /mcp add <name> to add one, or check your MCP config files.")

    lines: list[str] = []
    for s in statuses:
        if s.connected:
            status = "connected"
        elif s.last_error:
            status = "failed"
        else:
            status = "disconnected"

        trust = s.trust
        caps = f"tools={s.tool_count}, resources={s.resource_count}, prompts={s.prompt_count}" if s.connected else "tools=unknown, resources=unknown, prompts=unknown"

        lines.append(
            f"- {s.name}: {s.type}, {status}, trust={trust}, scope={s.scope}, {caps}"
        )
        if s.last_error and not s.connected:
            lines.append(f"  last error: {s.last_error[:200]}")

    return ToolResult(
        True,
        f"MCP support: enabled\nConfigured servers: {len(statuses)}\n" + "\n".join(lines),
    )


def _handle_connect_server(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Connect to a configured MCP server."""
    manager = _require_manager(ctx)
    name = str(args.get("name", ""))

    if not name:
        return ToolResult(False, "Server name is required.")

    # Check trust state first
    config_model, scope = manager.config_store.load_server(name)
    if config_model is None:
        return ToolResult(False, f"MCP server '{name}' not found in configuration.")

    if not config_model.enabled:
        return ToolResult(False, f"MCP server '{name}' is disabled. Use /mcp enable {name} to enable.")

    if config_model.trust == "ask":
        return ToolResult(
            False,
            f"MCP server '{name}' is not trusted yet and needs your approval first.\n"
            f"Run /mcp trust {name} to review and trust it, then /mcp connect {name}.",
        )

    if config_model.trust == "blocked":
        return ToolResult(False, f"MCP server '{name}' is blocked. Run /mcp trust {name} to unblock.")

    result = manager.connect_server(name)
    success = "Connected" in result or "already connected" in result
    return ToolResult(success, result)


def _handle_list_tools(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """List MCP tools from connected servers."""
    manager = _require_manager(ctx)
    server = args.get("server", "")

    all_infos = manager.tool_registry.get_all_tool_infos()

    if server:
        filtered = [(name, info) for name, info in all_infos if name.startswith(f"mcp__{server}__")]
    else:
        filtered = all_infos

    if not filtered:
        if server:
            msg = (
                f"No tools found for server '{server}'. "
                f"Make sure it is connected and has tools available."
            )
        else:
            msg = "No MCP tools available. Connect a server first."
        return ToolResult(True, msg)

    lines: list[str] = []
    for full_name, info in filtered:
        desc = (info.description or "(no description)")[:120]
        lines.append(f"- {full_name}: {desc}")

    return ToolResult(True, f"MCP tools ({len(lines)}):\n" + "\n".join(lines))


def _handle_call_tool(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Call an MCP tool on a connected server."""
    manager = _require_manager(ctx)
    server = str(args.get("server", ""))
    tool = str(args.get("tool", ""))
    tool_args = args.get("arguments", {})

    if not server or not tool:
        return ToolResult(False, "Both 'server' and 'tool' are required.")

    client = manager.get_client(server)
    if client is None or not client.is_connected:
        return ToolResult(False, f"MCP server '{server}' is not connected. Connect it first.")

    try:
        result = client.call_tool(tool, tool_args)
    except Exception as exc:
        return ToolResult(False, f"MCP tool call failed: {exc}")

    # Format the result content
    content_parts: list[str] = []
    for item in result.get("content", []):
        if item.get("type") == "text":
            content_parts.append(item.get("text", ""))
        elif item.get("type") == "resource":
            resource = item.get("resource", {})
            content_parts.append(str(resource.get("text", resource.get("blob", ""))))
        else:
            content_parts.append(str(item))

    content = "\n".join(content_parts)
    truncated = result.get("_truncated", False)
    if truncated:
        content += "\n[Output was truncated due to length.]"

    is_error = result.get("isError", False)
    return ToolResult(not is_error, content)


def _handle_list_resources(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """List MCP resources from connected servers."""
    manager = _require_manager(ctx)
    server = args.get("server", "")

    if server:
        resources = manager.resource_registry.get_server_resources(server)
    else:
        resources = []
        all_resources = []
        for name in manager.connected_servers:
            all_resources.extend(manager.resource_registry.get_server_resources(name))
        resources = all_resources

    if not resources:
        if server:
            msg = f"No resources found for server '{server}'."
        else:
            msg = "No MCP resources available."
        return ToolResult(True, msg)

    lines: list[str] = []
    for r in resources:
        desc = (r.description or "")[:80]
        lines.append(f"- {r.uri}: {r.name} ({r.mimeType or 'unknown'}) {desc}")

    return ToolResult(True, f"MCP resources ({len(lines)}):\n" + "\n".join(lines))


def _handle_read_resource(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Read a resource from an MCP server."""
    manager = _require_manager(ctx)
    server = str(args.get("server", ""))
    uri = str(args.get("uri", ""))

    if not server or not uri:
        return ToolResult(False, "Both 'server' and 'uri' are required.")

    if server not in manager.connected_servers:
        return ToolResult(False, f"MCP server '{server}' is not connected. Connect it first.")

    try:
        content = manager.read_resource(server, uri)
    except Exception as exc:
        return ToolResult(False, f"Failed to read MCP resource: {exc}")

    if content is None:
        return ToolResult(False, f"Resource '{uri}' not found on server '{server}'.")

    return ToolResult(True, content, data={"uri": uri, "server": server})


def _handle_list_prompts(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """List MCP prompts from connected servers."""
    manager = _require_manager(ctx)
    server = args.get("server", "")

    if server:
        prompts = manager.prompt_registry.get_server_prompts(server)
    else:
        prompts = []
        for name in manager.connected_servers:
            prompts.extend(manager.prompt_registry.get_server_prompts(name))

    if not prompts:
        if server:
            msg = f"No prompts found for server '{server}'."
        else:
            msg = "No MCP prompts available."
        return ToolResult(True, msg)

    lines: list[str] = []
    for p in prompts:
        desc = (p.description or "")[:80]
        arg_count = len(p.arguments)
        lines.append(f"- {p.name}: {desc} ({arg_count} arguments)")

    return ToolResult(True, f"MCP prompts ({len(lines)}):\n" + "\n".join(lines))


def _handle_get_prompt(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Fetch a prompt from an MCP server and return its content."""
    manager = _require_manager(ctx)
    server = str(args.get("server", ""))
    prompt = str(args.get("prompt", ""))
    prompt_args = args.get("arguments", {})

    if not server or not prompt:
        return ToolResult(False, "Both 'server' and 'prompt' are required.")

    if server not in manager.connected_servers:
        return ToolResult(False, f"MCP server '{server}' is not connected. Connect it first.")

    try:
        messages = manager.get_prompt_messages(server, prompt, prompt_args)
    except Exception as exc:
        return ToolResult(False, f"Failed to fetch MCP prompt: {exc}")

    if messages is None:
        return ToolResult(False, f"Prompt '{prompt}' not found on server '{server}'.")

    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", {})
        if isinstance(content, dict):
            text = content.get("text", str(content))
        else:
            text = str(content)
        lines.append(f"[{role}]\n{text}")

    return ToolResult(True, "\n---\n".join(lines))


def _handle_search_tools(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Search MCP tools by name, description, or schema."""
    manager = _require_manager(ctx)
    query = str(args.get("query", ""))
    server_filter = args.get("server", "")
    limit = int(args.get("limit", 10))

    if not query:
        return ToolResult(False, "Search query is required.")

    results = manager.tool_search.search(query, limit=limit)

    if server_filter:
        results = [(name, score) for name, score in results if name.startswith(f"mcp__{server_filter}__")]

    if not results:
        return ToolResult(True, f"No MCP tools found matching '{query}'.")

    lines: list[str] = [f"Found {len(results)} MCP tools matching '{query}':\n"]
    for full_name, score in results:
        info = manager.tool_registry.get_tool(full_name)
        if info:
            desc = (info.description or "(no description)")[:100]
            lines.append(f"- {full_name} (score: {score:.2f}): {desc}")
        else:
            lines.append(f"- {full_name}")

    return ToolResult(True, "\n".join(lines))


def _handle_add_server(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Add a new MCP server configuration."""
    manager = _require_manager(ctx)
    name = str(args.get("name", "")).strip()

    if not name:
        return ToolResult(False, "Server name is required.")

    transport = str(args.get("transport", "stdio"))
    valid_transports = ("stdio", "http", "streamable-http", "sse")
    if transport not in valid_transports:
        return ToolResult(False, f"Invalid transport '{transport}'. Valid: {', '.join(valid_transports)}")

    scope = str(args.get("scope", "project"))
    valid_scopes = ("user", "project", "local")
    if scope not in valid_scopes:
        return ToolResult(False, f"Invalid scope '{scope}'. Valid: {', '.join(valid_scopes)}")

    # Check if already exists
    existing, _ = manager.config_store.load_server(name)
    if existing is not None:
        return ToolResult(False, f"MCP server '{name}' already exists. Remove it first or use a different name.")

    trust = str(args.get("trust", "trusted"))
    if trust not in ("trusted", "ask"):
        trust = "trusted"

    server_config: dict[str, Any] = {
        "type": transport,
        "enabled": True,
        "trust": trust,
        "timeout_ms": int(args.get("timeout_ms", 60000)),
        "max_output_tokens": int(args.get("max_output_tokens", 25000)),
    }

    if transport == "stdio":
        command = str(args.get("command", ""))
        if not command:
            return ToolResult(False, "For stdio transport, 'command' is required (e.g. 'npx', 'uvx', 'python').")
        server_config["command"] = command
        server_config["args"] = list(args.get("args", []))
        server_config["env"] = args.get("env", {}) or {}
    elif transport in ("http", "streamable-http", "sse"):
        url = str(args.get("url", ""))
        if not url:
            return ToolResult(False, "For HTTP transport, 'url' is required.")
        server_config["url"] = url
        server_config["headers"] = args.get("headers", {}) or {}

    try:
        from donovanagent.mcp.config import McpServerConfigModel
        model = McpServerConfigModel(**server_config)
    except Exception as exc:
        return ToolResult(False, f"Invalid server config: {exc}")

    manager.config_store.save_server(name, model, scope)  # type: ignore

    # Auto-connect unless opted out
    auto_connect = args.get("auto_connect", True)
    connected = False
    if auto_connect:
        try:
            result = manager.connect_server(name)
            connected = "Connected" in result
            if connected:
                status = manager.get_server_status(name)
                cap_summary = f"Tools: {status.tool_count}, Resources: {status.resource_count}, Prompts: {status.prompt_count}"
            else:
                cap_summary = result
        except Exception as exc:
            cap_summary = str(exc)
    else:
        cap_summary = "Not connected (auto_connect disabled). Run 'donovan mcp connect' to connect."

    if connected:
        return ToolResult(True, f"MCP server '{name}' added and connected. {cap_summary}")
    else:
        return ToolResult(True, f"MCP server '{name}' added. {cap_summary}")


def _handle_remove_server(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    """Remove a configured MCP server."""
    manager = _require_manager(ctx)
    name = str(args.get("name", ""))
    scope: str | None = args.get("scope", None)

    if not name:
        return ToolResult(False, "Server name is required.")

    # Disconnect if connected
    if name in manager.connected_servers:
        try:
            manager.disconnect_server(name)
        except Exception:
            pass

    config_scope = None if scope is None else scope
    if manager.config_store.remove_server(name, config_scope):  # type: ignore
        return ToolResult(True, f"MCP server '{name}' removed.")
    else:
        return ToolResult(False, f"MCP server '{name}' not found in configuration.")


# ---------------------------------------------------------------------------
# Schemas for each control tool
# ---------------------------------------------------------------------------

SERVER_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

SERVER_CONNECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Name of the MCP server to connect."},
    },
    "required": ["name"],
    "additionalProperties": False,
}

TOOL_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {
            "type": "string",
            "description": "Optional server name to filter by. If omitted, tools from all servers shown.",
        },
    },
    "additionalProperties": False,
}

TOOL_CALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "MCP server name."},
        "tool": {"type": "string", "description": "MCP tool name to call."},
        "arguments": {
            "type": "object",
            "description": "Arguments to pass to the MCP tool.",
            "additionalProperties": True,
        },
    },
    "required": ["server", "tool", "arguments"],
    "additionalProperties": False,
}

RESOURCE_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {
            "type": "string",
            "description": "Optional server name to filter by.",
        },
    },
    "additionalProperties": False,
}

RESOURCE_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "MCP server name."},
        "uri": {"type": "string", "description": "Resource URI to read."},
    },
    "required": ["server", "uri"],
    "additionalProperties": False,
}

PROMPT_LIST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {
            "type": "string",
            "description": "Optional server name to filter by.",
        },
    },
    "additionalProperties": False,
}

PROMPT_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "server": {"type": "string", "description": "MCP server name."},
        "prompt": {"type": "string", "description": "Prompt name."},
        "arguments": {
            "type": "object",
            "description": "Optional prompt arguments.",
            "additionalProperties": True,
        },
    },
    "required": ["server", "prompt"],
    "additionalProperties": False,
}

SEARCH_TOOLS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query."},
        "server": {
            "type": "string",
            "description": "Optional server name to filter results.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum results to return (default 10).",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

SERVER_ADD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Name for the MCP server."},
        "transport": {
            "type": "string",
            "enum": ["stdio", "http", "streamable-http", "sse"],
            "description": "Transport type. stdio for local processes, http/streamable-http/sse for remote endpoints.",
        },
        "command": {
            "type": "string",
            "description": "Command to run (for stdio transport). E.g. 'npx', 'uvx', 'python', 'node'.",
        },
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Command arguments as an array of strings (for stdio transport).",
        },
        "url": {
            "type": "string",
            "description": "Server URL (for http/streamable-http/sse transport).",
        },
        "env": {
            "type": "object",
            "description": "Optional environment variables as KEY: VALUE pairs.",
            "additionalProperties": {"type": "string"},
        },
        "headers": {
            "type": "object",
            "description": "Optional HTTP headers as Name: Value pairs (for http transport).",
            "additionalProperties": {"type": "string"},
        },
        "scope": {
            "type": "string",
            "enum": ["user", "project", "local"],
            "description": "Config scope (default: project).",
        },
        "timeout_ms": {
            "type": "integer",
            "description": "Request timeout in milliseconds (default 60000).",
        },
        "trust": {
            "type": "string",
            "enum": ["trusted", "ask"],
            "description": "Trust setting. 'trusted' to auto-trust, 'ask' to require user approval.",
        },
        "auto_connect": {
            "type": "boolean",
            "description": "Whether to connect immediately after adding (default: true).",
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}

SERVER_REMOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Name of the MCP server to remove."},
        "scope": {
            "type": "string",
            "enum": ["user", "project", "local"],
            "description": "Optional config scope to remove from.",
        },
    },
    "required": ["name"],
    "additionalProperties": False,
}

CONTROL_TOOL_DEFS: list[ToolDefinition] = [
    ToolDefinition(
        name="donovan_mcp_list_servers",
        description="List configured MCP servers and their current status, trust state, "
                    "transport type, scope, and capability counts for connected servers. "
                    "Use this to check what MCP servers are available.",
        enabled_key="mcp_tools.enabled",
        parameters=SERVER_LIST_SCHEMA,
        handler=_handle_list_servers,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_connect_server",
        description="Connect to a configured MCP server. The server must be trusted first. "
                    "If trust is required, this tool will tell you and suggest the approval flow.",
        enabled_key="mcp_tools.enabled",
        parameters=SERVER_CONNECT_SCHEMA,
        handler=_handle_connect_server,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_list_tools",
        description="List MCP tools from connected servers. Filter by server name if needed.",
        enabled_key="mcp_tools.enabled",
        parameters=TOOL_LIST_SCHEMA,
        handler=_handle_list_tools,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_call_tool",
        description="Call a tool exposed by an MCP server. The server must be connected. "
                    "Use donovan_mcp_list_tools first to discover available tools.",
        enabled_key="mcp_tools.enabled",
        parameters=TOOL_CALL_SCHEMA,
        handler=_handle_call_tool,
        requires_approval=False,
        risk="medium",
    ),
    ToolDefinition(
        name="donovan_mcp_list_resources",
        description="List resources exposed by connected MCP servers. Filter by server name if needed.",
        enabled_key="mcp_tools.enabled",
        parameters=RESOURCE_LIST_SCHEMA,
        handler=_handle_list_resources,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_read_resource",
        description="Read a resource from a connected MCP server by URI.",
        enabled_key="mcp_tools.enabled",
        parameters=RESOURCE_READ_SCHEMA,
        handler=_handle_read_resource,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_list_prompts",
        description="List prompts exposed by connected MCP servers. Filter by server name if needed.",
        enabled_key="mcp_tools.enabled",
        parameters=PROMPT_LIST_SCHEMA,
        handler=_handle_list_prompts,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_get_prompt",
        description="Fetch a prompt from an MCP server and return its content and messages.",
        enabled_key="mcp_tools.enabled",
        parameters=PROMPT_GET_SCHEMA,
        handler=_handle_get_prompt,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="search_mcp_tools",
        description="Search MCP tools by name, description, or schema across all connected servers. "
                    "Use this when you need a specific MCP capability but don't know which tool to call.",
        enabled_key="mcp_tools.enabled",
        parameters=SEARCH_TOOLS_SCHEMA,
        handler=_handle_search_tools,
        requires_approval=False,
        risk="low",
    ),
    ToolDefinition(
        name="donovan_mcp_add_server",
        description="Add a new MCP server configuration and optionally connect to it. "
                    "Supports stdio (local process) and HTTP transports. "
                    "For stdio: provide command + args array. "
                    "For HTTP: provide url + optional headers. "
                    "Use this when the user asks you to set up an MCP server.",
        enabled_key="mcp_tools.enabled",
        parameters=SERVER_ADD_SCHEMA,
        handler=_handle_add_server,
        requires_approval=False,
        risk="medium",
    ),
    ToolDefinition(
        name="donovan_mcp_remove_server",
        description="Remove a configured MCP server configuration. "
                    "Use this when the user wants to delete an MCP server setup.",
        enabled_key="mcp_tools.enabled",
        parameters=SERVER_REMOVE_SCHEMA,
        handler=_handle_remove_server,
        requires_approval=False,
        risk="medium",
    ),
]


# ---------------------------------------------------------------------------
# DSML/internal tool-call parser
# ---------------------------------------------------------------------------

import json as _json
import re as _re


def _coerce_dsml_value(raw: str, is_string: bool | None) -> Any:
    """Coerce a DSML parameter value to the appropriate Python type.

    When ``is_string`` is True, the value is always kept as a string.
    When ``is_string`` is False, attempt JSON parse (handles numbers,
    booleans, arrays, dicts), falling back to string.
    When ``is_string`` is None (no string attribute), attempt JSON parse,
    falling back to string.
    """
    if is_string:
        return raw

    # Attempt JSON coercion for non-string values
    stripped = raw.strip()
    if not stripped:
        return raw

    # Try JSON parse first (handles numbers, booleans, null, arrays, objects)
    try:
        return _json.loads(stripped)
    except (_json.JSONDecodeError, ValueError):
        pass

    # Try integer
    try:
        return int(stripped)
    except ValueError:
        pass

    # Try float
    try:
        return float(stripped)
    except ValueError:
        pass

    # Boolean literals
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False

    return raw


def parse_dsml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse DSML/internal tool-call markup from model output.

    Detects formatted blocks such as:

    .. code-block:: xml

        <tool_calls>
          <invoke name="toolName">
            <parameter name="argName" string="true">value</parameter>
            <parameter name="count" string="false">5</parameter>
          </invoke>
        </tool_calls>

    Also detects standalone ``<invoke>`` blocks not wrapped in
    ``<tool_calls>``.

    Handles:
    - ``string="true"`` — value kept as string
    - ``string="false"`` — value coerced to bool/number/JSON
    - No ``string`` attribute — auto-detected via JSON parse
    - Arbitrary attribute ordering on ``<parameter>`` tags

    Returns a list of dicts with keys ``name`` and ``arguments``.
    """
    calls: list[dict[str, Any]] = []

    if not text:
        return calls

    # Find <tool_calls>...</tool_calls> blocks (non-greedy inner)
    tc_blocks = _re.findall(
        r'<tool_calls[^>]*>(.*?)</tool_calls\s*>',
        text, _re.DOTALL | _re.IGNORECASE,
    )
    if not tc_blocks:
        # Fallback: find <invoke> tags not wrapped in tool_calls
        tc_blocks = _re.findall(
            r"(<invoke\s+name=['\"][^'\"]*['\"][^>]*>.*?</invoke\s*>)",
            text, _re.DOTALL | _re.IGNORECASE,
        )

    for block in tc_blocks:
        # Find all <invoke> blocks within this block
        invoke_blocks = _re.findall(
            r"<invoke\s+name=['\"][^'\"]*['\"][^>]*>.*?</invoke\s*>",
            block, _re.DOTALL | _re.IGNORECASE,
        )
        if not invoke_blocks:
            continue

        for invoke_block in invoke_blocks:
            # Extract tool name
            name_match = _re.search(
                r"<invoke\s+name=['\"]([^'\"]*)['\"]",
                invoke_block, _re.IGNORECASE,
            )
            if not name_match:
                continue
            name = name_match.group(1)

            # Extract parameters — handles arbitrary attribute ordering
            params: dict[str, Any] = {}
            for p_match in _re.finditer(
                r"<parameter\s+(?P<attrs>[^>]*)>(?P<value>.*?)</parameter\s*>",
                invoke_block, _re.DOTALL | _re.IGNORECASE,
            ):
                attrs = p_match.group("attrs")
                raw_value = p_match.group("value")

                # Extract the name attribute value
                name_attr = _re.search(
                    r"""name\s*=\s*['\"]([^'\"]*)['\"]""",
                    attrs, _re.IGNORECASE | _re.VERBOSE,
                )
                if not name_attr:
                    continue
                param_name = name_attr.group(1)

                # Extract the string attribute (presence and truth value)
                str_attr = _re.search(
                    r"""string\s*=\s*['\"](true|false)['\"]""",
                    attrs, _re.IGNORECASE,
                )
                is_string: bool | None = None
                if str_attr:
                    is_string = str_attr.group(1).lower() == "true"

                params[param_name] = _coerce_dsml_value(raw_value, is_string)

            if name:
                calls.append({"name": name, "arguments": params})

    return calls


# ---------------------------------------------------------------------------
# Tool-name repair for DSML extracted calls
# ---------------------------------------------------------------------------


def _repair_camel_to_snake(name: str) -> str:
    """Convert a camelCase name to snake_case.

    Handles acronyms correctly: parseJSON -> parse_json,
    getSystemInfo -> get_system_info, parseJSONString -> parse_json_string.
    """
    # Insert underscore before uppercase that follows lowercase
    name = _re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    # Insert underscore between adjacent uppercase when followed by lowercase
    name = _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return name.lower()


def repair_tool_name(
    raw_name: str,
    registered_names: set[str],
) -> str | None:
    """Attempt to repair a malformed tool name to a registered name.

    Returns the repaired name if an exact unique match is found, or None.

    Repair strategies in order:
    1. Exact match — return as-is
    2. Normalize separators for MCP: single ``_``, ``-``, ``.`` → ``__``
    3. camelCase → snake_case for non-MCP tools
    4. Concatenated MCP names (``mcpframer...`` → ``mcp__framer__...``)
    5. Fuzzy prefix match for MCP tools

    Safety rules:
    - Only repair if exactly one registered tool matches.
    - Never repair to a known destructive tool unless the original name
      unambiguously matches.
    """
    # 1) Exact match
    if raw_name in registered_names:
        return raw_name

    # 2) MCP separator normalization: single _, -, . → __
    #    Skip if already has __ to avoid mangling mixed separators
    if "__" not in raw_name:
        for sep in ("_", "-", "."):
            parts = raw_name.split(sep)
            if len(parts) >= 2:
                candidate = "__".join(parts)
                if candidate in registered_names:
                    return candidate
                # Also try snake_case on the tool segment (last part after __)
                if candidate.count("__") >= 2:
                    *prefix_parts, tool_part = candidate.rsplit("__", 1)
                    tool_snake = _repair_camel_to_snake(tool_part)
                    if tool_snake != tool_part:
                        snake_candidate = "__".join([*prefix_parts, tool_snake])
                        if snake_candidate in registered_names:
                            return snake_candidate

    # 3) camelCase → snake_case for non-MCP tools
    snake = _repair_camel_to_snake(raw_name)
    if snake in registered_names:
        return snake

    # 4) Concatenated MCP: "mcpframerX" or "mcp_framer_X" → split into mcp + framer + X
    #    Try every character boundary in the rest after "mcp"
    lowered = raw_name.lower()
    if "mcp" in lowered and "__" not in raw_name:
        mcp_idx = lowered.index("mcp")
        rest = raw_name[mcp_idx + 3:]  # everything after "mcp"
        # Strip leading separators so "mcp_framer_X" rest = "framer_X", not "_framer_X"
        rest = rest.lstrip("_-.")
        if rest:
            for split_point in range(1, len(rest)):
                server_part = rest[:split_point]
                tool_part = rest[split_point:]
                candidate = f"mcp__{server_part}__{tool_part}"
                if candidate in registered_names:
                    return candidate
                # Also try camelCase-to-snake on the tool part
                tool_snake = _repair_camel_to_snake(tool_part)
                if tool_snake != tool_part:
                    candidate2 = f"mcp__{server_part}__{tool_snake}"
                    if candidate2 in registered_names:
                        return candidate2

            # Also try splitting rest by underscore if present
            if "_" in rest:
                under_parts = rest.split("_", 1)
                server_under = under_parts[0]
                tool_under = under_parts[1]
                candidate_u = f"mcp__{server_under}__{tool_under}"
                if candidate_u in registered_names:
                    return candidate_u
                # Also try camelCase-to-snake on the tool part
                tool_under_snake = _repair_camel_to_snake(tool_under)
                if tool_under_snake != tool_under:
                    candidate_u2 = f"mcp__{server_under}__{tool_under_snake}"
                    if candidate_u2 in registered_names:
                        return candidate_u2

    # 5) Substring match for MCP tools (prefix-based only)
    mcp_candidates = [n for n in registered_names if n.startswith("mcp__") and raw_name.lower() in n.lower()]
    if len(mcp_candidates) == 1:
        return mcp_candidates[0]

    # 6) Fixed first separator for MCP names with mixed separators
    if raw_name.startswith("mcp_") and not raw_name.startswith("mcp__"):
        candidate = "mcp__" + raw_name[4:]
        if candidate in registered_names:
            return candidate

    return None


def extract_internal_tool_calls(
    text: str,
    registered_names: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Extract DSML/internal tool-call markup from model output text.

    This is the higher-level entry point used by the agent loop.
    Unlike ``parse_dsml_tool_calls`` which only returns calls, this also
    returns the text cleaned of all DSML markup so callers can decide
    whether to display, suppress, or store the leading content.

    Args:
        text: Raw model output text.
        registered_names: Optional set of tool names registered in the
            system. When provided, extracted tool names are repaired via
            ``repair_tool_name()`` to match registered names.

    Returns:
        ``(cleaned_text, tool_calls)`` where *cleaned_text* is the original
        text with all DSML blocks removed, and *tool_calls* is a list of
        ``{"name": ..., "arguments": ...}`` dicts.

        If no DSML markup is found, ``cleaned_text == text`` and
        ``tool_calls == []``.
    """
    calls = parse_dsml_tool_calls(text)
    if not calls:
        return text, []

    # Attempt name repair for each extracted call
    if registered_names:
        for call in calls:
            raw_name = call["name"]
            repaired = repair_tool_name(raw_name, registered_names)
            if repaired is not None and repaired != raw_name:
                logger.info("Repaired internal tool name %s -> %s", raw_name, repaired)
                call["name"] = repaired

    # Remove DSML blocks
    from donovanagent.ui.render import strip_tool_markup

    cleaned = strip_tool_markup(text)
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


# ---------------------------------------------------------------------------
# XML validation for MCP tool payloads
# ---------------------------------------------------------------------------


def validate_mcp_xml(xml_str: str) -> tuple[bool, str]:
    """Basic XML validation before sending to Framer MCP update tools.

    Checks:
    - Well-formed angle brackets (every ``<`` tag has a matching ``>``)
    - No unclosed tags
    - No invalid nesting

    Returns ``(is_valid, error_message)``.
    """
    if not xml_str or not xml_str.strip():
        return False, "XML content is empty."

    # Check for unclosed angle brackets
    lines = xml_str.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        opens = stripped.count("<")
        closes = stripped.count(">")
        if opens > closes:
            return False, f"Line {i}: Missing closing '>' in: {stripped[:80]}"
        if closes > opens:
            return False, f"Line {i}: Extra '>' without opening '<' in: {stripped[:80]}"

    # Check for unclosed tags (opening tag without matching closing)
    # Only check non-self-closing, non-declaration tags
    tag_pattern = _re.compile(r"<(/?)(\w[\w.-]*)([^>]*?)(/?)>")
    stack: list[tuple[str, int]] = []  # (tag_name, line_number)

    for i, line in enumerate(lines, 1):
        for match in tag_pattern.finditer(line):
            is_close = bool(match.group(1))
            tag_name = match.group(2)
            is_self_close = bool(match.group(4))

            if tag_name.lower() in ("!--", "?", "![cdata["):
                continue

            if is_self_close:
                continue
            if is_close:
                if stack and stack[-1][0] == tag_name:
                    stack.pop()
                else:
                    # Try to find a matching open higher up, or just warn
                    for j in range(len(stack) - 1, -1, -1):
                        if stack[j][0] == tag_name:
                            stack.pop(j)
                            break
            else:
                stack.append((tag_name, i))

    if stack:
        details = ", ".join(f"<{t}> at line {l}" for t, l in stack[:5])
        return False, f"Unclosed tag(s): {details}"

    return True, ""


# ---------------------------------------------------------------------------
# MCP write tool risk classification
# ---------------------------------------------------------------------------

_WRITE_KEYWORDS = frozenset({
    "update", "set", "insert", "create", "delete", "remove",
    "publish", "deploy", "overwrite", "write", "modify", "rename",
    "move", "copy", "add", "upload", "put", "destroy", "truncate",
    "purge", "wipe", "format", "kill", "stop", "terminate",
})


def is_mcp_write_tool(tool_name: str) -> bool:
    """Check if an MCP tool name suggests write/destructive access.

    Handles camelCase (``updateXmlForNode``), snake_case
    (``delete_record``), and kebab-case (``set-value``).
    """
    parts = _re.split(r"[_.-]+", tool_name)
    words: list[str] = []
    for part in parts:
        words.extend(_re.findall(r"[a-z]+|[A-Z][a-z]*|[A-Z]+(?=[A-Z]|$)", part))
    return any(w.lower() in _WRITE_KEYWORDS for w in words)
