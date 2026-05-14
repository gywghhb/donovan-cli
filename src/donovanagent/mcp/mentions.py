"""MCP @ resource mention parsing.

Supports @server:protocol://path syntax in user messages.
"""

from __future__ import annotations

import re
from typing import Any

from donovanagent.mcp.manager import McpManager
from donovanagent.mcp.protocol import McpError
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

# Pattern: @server:protocol://path
# Examples:
#   @github:issue://123
#   @docs:file://api/authentication
#   @postgres:schema://users
_MENTION_PATTERN = re.compile(
    r"@([a-zA-Z0-9_-]+):([a-zA-Z][a-zA-Z0-9+.-]*://[^\s,;)}]+)"
)


def parse_mentions(text: str) -> list[dict[str, str]]:
    """Parse @server:protocol://path mentions from text.

    Returns list of dicts with keys: server, uri, full_match.
    """
    mentions: list[dict[str, str]] = []
    for match in _MENTION_PATTERN.finditer(text):
        mentions.append({
            "server": match.group(1),
            "uri": match.group(2),
            "full_match": match.group(0),
        })
    return mentions


def resolve_mentions(text: str, mcp_manager: McpManager) -> tuple[str, list[str]]:
    """Resolve @ mentions in text by fetching MCP resources.

    Returns (text_with_references_replaced, list_of_attached_resources).

    For each mention:
    - If the server is connected, fetch the resource and attach its content.
    - If the server is configured but not connected, add a note.
    - If the server is unknown, leave the mention as-is.
    """
    mentions = parse_mentions(text)
    if not mentions:
        return text, []

    attachments: list[str] = []

    for mention in mentions:
        server_name = mention["server"]
        uri = mention["uri"]
        full_match = mention["full_match"]

        # Check if server is connected
        client = mcp_manager.get_client(server_name)
        if client is None or not client.is_connected:
            # Check if server is configured
            config, _ = mcp_manager.config_store.load_server(server_name)
            if config is not None:
                replacement = (
                    f"[Resource belongs to MCP server '{server_name}', "
                    f"but it is not connected. Run /mcp connect {server_name} to connect.]"
                )
                text = text.replace(full_match, replacement)
            else:
                # Unknown server, leave as-is
                pass
            continue

        # Fetch the resource
        try:
            content = client.read_resource(uri)
            if content is None:
                attachments.append(f"[MCP Resource: {uri} — not found]")
                continue

            text = text.replace(
                full_match,
                f"[Attached MCP resource from {server_name}: {uri}]"
            )

            attachments.append(
                f"--- MCP Resource: {uri} (server: {server_name}) ---\n"
                f"{content.text or content.blob or ''}"
            )
        except McpError as exc:
            attachments.append(
                f"[MCP Resource: {uri} — error: {exc.message}]"
            )
        except Exception as exc:
            logger.warning("Failed to fetch MCP resource %s: %s", uri, exc)
            attachments.append(
                f"[MCP Resource: {uri} — error: {exc}]"
            )

    return text, attachments


def format_attachments(attachments: list[str]) -> str:
    """Format resource attachments as context for the model."""
    if not attachments:
        return ""
    sections = "\n\n---\n\n".join(attachments)
    return f"\n\n--- Attached MCP Resources ---\n\n{sections}\n\n--- End Attached Resources ---"
