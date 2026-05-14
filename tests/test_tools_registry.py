from __future__ import annotations

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.tools.registry import build_default_registry


def test_registry_contains_required_tools() -> None:
    registry = build_default_registry(DonovanAgentConfig())
    names = {tool.name for tool in registry.list()}
    assert {
        "run_shell",
        "read_file",
        "write_file",
        "patch_file",
        "list_directory",
        "search_files",
        "get_system_info",
        "web_search",
        "execute",
        "process_status",
        "kill_process",
    } <= names


def test_openai_tool_schema() -> None:
    registry = build_default_registry(DonovanAgentConfig())
    schema = registry.get("read_file").openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "read_file"
    assert schema["function"]["parameters"]["type"] == "object"
