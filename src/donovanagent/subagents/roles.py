from __future__ import annotations

# ---------------------------------------------------------------------------
# Subagent role presets
# Each role defines the tools the subagent may call, the permission level,
# and a description that serves as the system prompt.
#
# To add a new role:
#   1. Add a new dict with "tools", "permissions", and "description" keys.
#   2. Optionally add a SubagentRole enum entry in models.py.
#   3. The role is immediately available via /subagents create <role> "..."
# ---------------------------------------------------------------------------

ROLE_PRESETS = {
    "researcher": {
        "name": "Researcher",
        "tools": ["search_files", "read_file", "web_search", "get_system_info"],
        "permissions": "read_only",
        "description": (
            "You are a Research subagent. Your goal is to gather information "
            "from local files or the web. Use search and read tools to collect data. "
            "Do NOT write or modify any files."
        ),
    },
    "coder": {
        "name": "Coder",
        "tools": ["read_file", "write_file", "run_shell", "execute", "search_files"],
        "permissions": "write",
        "description": (
            "You are a Coding subagent. Your goal is to read, write, and modify code. "
            "Use shell commands, execute Python, and edit files as needed. "
            "Report what you changed and why."
        ),
    },
    "tester": {
        "name": "Tester",
        "tools": ["run_shell", "read_file"],
        "permissions": "read_only",
        "description": (
            "You are a Testing subagent. Run test suites and report results. "
            "Do NOT modify any files."
        ),
    },
    "reviewer": {
        "name": "Reviewer",
        "tools": ["read_file", "search_files", "get_system_info"],
        "permissions": "read_only",
        "description": (
            "You are a Code Review subagent. Review code diffs for bugs, "
            "security issues, and style problems. Do NOT modify any files."
        ),
    },
    "safety": {
        "name": "Safety Reviewer",
        "tools": ["read_file", "search_files", "get_system_info"],
        "permissions": "read_only",
        "description": (
            "You are a Safety Review subagent. Inspect planned commands and "
            "file edits for dangerous or destructive operations. Flag any risks. "
            "Do NOT modify any files."
        ),
    },
    "browser_qa": {
        "name": "Browser QA",
        "tools": [
            "browser_open", "browser_snapshot", "browser_screenshot",
            "browser_click", "browser_type", "browser_press",
            "browser_extract_links", "browser_current_url",
            "browser_back", "browser_reload", "browser_get_html",
        ],
        "permissions": "read_only",
        "description": (
            "You are a Browser QA subagent. Open pages, take screenshots, "
            "click elements, type text, and verify web page behavior. "
            "Do NOT modify any files."
        ),
    },
    "planner": {
        "name": "Planner",
        "tools": ["read_file", "search_files", "web_search", "get_system_info"],
        "permissions": "read_only",
        "description": (
            "You are a Planning subagent. Analyze the task and create a "
            "detailed step-by-step plan. Do NOT execute any changes."
        ),
    },
    "custom": {
        "name": "Custom",
        "tools": [],
        "permissions": "custom",
        "description": (
            "You are a Custom subagent. Your tools and goals are defined "
            "by the user. Follow the task description precisely."
        ),
    },
}
