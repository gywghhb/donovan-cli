"""MCP JSON-RPC protocol message types and helpers."""

from __future__ import annotations

import json
import uuid
from typing import Any

from donovanagent import __version__ as _donovan_version


def json_rpc_request(method: str, params: dict[str, Any] | None = None) -> str:
    """Create a JSON-RPC 2.0 request string."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return json.dumps(msg, ensure_ascii=False)


def json_rpc_notification(method: str, params: dict[str, Any] | None = None) -> str:
    """Create a JSON-RPC 2.0 notification (no id)."""
    msg: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        msg["params"] = params
    return json.dumps(msg, ensure_ascii=False)


def parse_json_rpc(line: str) -> dict[str, Any]:
    """Parse a JSON-RPC message from a string."""
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON-RPC message: {exc}") from exc
    if not isinstance(msg, dict):
        raise ValueError("JSON-RPC message must be a JSON object")
    if msg.get("jsonrpc") != "2.0":
        raise ValueError("Not a JSON-RPC 2.0 message")
    return msg


class McpError(Exception):
    """MCP protocol-level error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[MCP Error {code}] {message}")

    @classmethod
    def from_rpc(cls, error_obj: dict[str, Any]) -> McpError:
        return cls(
            code=error_obj.get("code", -1),
            message=error_obj.get("message", "Unknown error"),
            data=error_obj.get("data"),
        )


# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP-specific error codes
MCP_TOOL_NOT_FOUND = -32000
MCP_RESOURCE_NOT_FOUND = -32001
MCP_PROMPT_NOT_FOUND = -32002
MCP_UNAUTHORIZED = -32003
MCP_TIMEOUT = -32004
MCP_OUTPUT_TOO_LARGE = -32005


# Latest MCP protocol version supported by this client.
_MCP_PROTOCOL_VERSION = "2024-11-05"


def set_mcp_protocol_version(version: str) -> None:
    """Override the default protocol version (for testing)."""
    global _MCP_PROTOCOL_VERSION
    _MCP_PROTOCOL_VERSION = version


def get_mcp_protocol_version() -> str:
    return _MCP_PROTOCOL_VERSION


def make_initialize_params(
    client_name: str = "DonovanAgent",
    client_version: str = _donovan_version,
    protocol_version: str | None = None,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build MCP initialize request parameters."""
    return {
        "protocolVersion": protocol_version or _MCP_PROTOCOL_VERSION,
        "capabilities": capabilities or {
            "tools": {},
            "resources": {},
            "prompts": {},
        },
        "clientInfo": {
            "name": client_name,
            "version": client_version,
        },
    }


def make_list_tools_params(cursor: str | None = None) -> dict[str, Any] | None:
    """Build tools/list parameters (cursor for pagination)."""
    if cursor:
        return {"cursor": cursor}
    return None


def make_call_tool_params(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build tools/call parameters."""
    params: dict[str, Any] = {"name": name}
    if arguments:
        params["arguments"] = arguments
    return params


def make_list_resources_params(cursor: str | None = None) -> dict[str, Any] | None:
    """Build resources/list parameters."""
    if cursor:
        return {"cursor": cursor}
    return None


def make_read_resource_params(uri: str) -> dict[str, Any]:
    """Build resources/read parameters."""
    return {"uri": uri}


def make_list_prompts_params(cursor: str | None = None) -> dict[str, Any] | None:
    """Build prompts/list parameters."""
    if cursor:
        return {"cursor": cursor}
    return None


def make_get_prompt_params(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build prompts/get parameters."""
    params: dict[str, Any] = {"name": name}
    if arguments:
        params["arguments"] = arguments
    return params
