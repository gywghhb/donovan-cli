"""Fake MCP stdio server for testing.

Implements the MCP protocol over stdio with:
- initialize / notifications/initialized
- tools/list -> echo, list_projects
- tools/call -> echo, list_projects
- resources/list -> docs:file://hello
- resources/read -> docs:file://hello
- prompts/list -> summarize_project
- prompts/get -> summarize_project
"""

from __future__ import annotations

import json
import sys
from typing import Any

INITIALIZED = False

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the input message",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to echo back",
                }
            },
            "required": ["message"],
        },
    },
    {
        "name": "list_projects",
        "description": "List available projects",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "read_file_content",
        "description": "Read content from a file (MOCK)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                }
            },
            "required": ["path"],
        },
    },
]

RESOURCES = [
    {
        "uri": "docs:file://hello",
        "name": "Hello Document",
        "description": "A sample hello document",
        "mimeType": "text/plain",
    },
    {
        "uri": "docs:file://readme",
        "name": "README",
        "description": "Project readme file",
        "mimeType": "text/markdown",
    },
]

PROMPTS = [
    {
        "name": "summarize_project",
        "description": "Generate a project summary",
        "arguments": [
            {
                "name": "project_name",
                "description": "The name of the project to summarize",
                "required": True,
            }
        ],
    },
    {
        "name": "greeting",
        "description": "Generate a greeting message",
        "arguments": [
            {
                "name": "name",
                "description": "Name to greet",
                "required": True,
            }
        ],
    },
]


def send_response(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def send_result(request_id: Any, result: Any) -> None:
    send_response({"jsonrpc": "2.0", "id": request_id, "result": result})


def send_error(request_id: Any, code: int, message: str) -> None:
    send_response({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    })


def handle_request(msg: dict[str, Any]) -> None:
    global INITIALIZED
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        INITIALIZED = True
        send_result(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "fake-test-server",
                "version": "1.0.0",
            },
        })
    elif method == "notifications/initialized":
        pass  # No response for notifications
    elif method == "tools/list":
        if not INITIALIZED:
            send_error(msg_id, -32000, "Not initialized")
            return
        send_result(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        if not INITIALIZED:
            send_error(msg_id, -32000, "Not initialized")
            return
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name == "echo":
            message = arguments.get("message", "")
            send_result(msg_id, {
                "content": [{"type": "text", "text": f"Echo: {message}"}],
            })
        elif tool_name == "list_projects":
            send_result(msg_id, {
                "content": [{"type": "text", "text": "Projects: alpha, beta, gamma"}],
            })
        elif tool_name == "read_file_content":
            path = arguments.get("path", "")
            send_result(msg_id, {
                "content": [{"type": "text", "text": f"Content of {path}:\nHello from fake MCP server!"}],
            })
        else:
            send_error(msg_id, -32601, f"Tool not found: {tool_name}")
    elif method == "resources/list":
        if not INITIALIZED:
            send_error(msg_id, -32000, "Not initialized")
            return
        send_result(msg_id, {"resources": RESOURCES})
    elif method == "resources/read":
        if not INITIALIZED:
            send_error(msg_id, -32000, "Not initialized")
            return
        uri = params.get("uri", "")
        for res in RESOURCES:
            if res["uri"] == uri:
                send_result(msg_id, {
                    "contents": [{
                        "uri": uri,
                        "mimeType": res["mimeType"],
                        "text": f"Content of {res['name']}: Hello world!",
                    }]
                })
                return
        send_error(msg_id, -32002, f"Resource not found: {uri}")
    elif method == "prompts/list":
        if not INITIALIZED:
            send_error(msg_id, -32000, "Not initialized")
            return
        send_result(msg_id, {"prompts": PROMPTS})
    elif method == "prompts/get":
        if not INITIALIZED:
            send_error(msg_id, -32000, "Not initialized")
            return
        prompt_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if prompt_name == "summarize_project":
            project = arguments.get("project_name", "unknown")
            send_result(msg_id, {
                "description": f"Summary for {project}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Please summarize the project '{project}'.",
                        },
                    }
                ],
            })
        elif prompt_name == "greeting":
            name = arguments.get("name", "World")
            send_result(msg_id, {
                "description": f"Greeting for {name}",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Say hello to {name} from the MCP server.",
                        },
                    }
                ],
            })
        else:
            send_error(msg_id, -32601, f"Prompt not found: {prompt_name}")
    elif method == "exit":
        sys.exit(0)
    else:
        send_error(msg_id, -32601, f"Method not found: {method}")


def main() -> None:
    """Run the fake MCP stdio server."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            send_response({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            })
            continue

        try:
            handle_request(msg)
        except Exception as exc:
            send_response({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            })


if __name__ == "__main__":
    main()
