from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from donovanagent import __version__
from donovanagent.app import DonovanAgentApp, run_doctor, search_results_table
from donovanagent.config.manager import ConfigManager
from donovanagent.config.wizard import configure_model, run_setup_wizard
from donovanagent.memory import MemoryDatabase
from donovanagent.memory.project_context import detect_project_context
from donovanagent.tools.registry import build_default_registry
from donovanagent.tools.web import TavilySearchProvider
from donovanagent.ui.render import (
    assistant_panel,
    config_table,
    context_footer,
    error_panel,
    info_panel,
    sessions_table,
    tools_table,
    tools_used_panel,
)
from donovanagent.mcp.config import McpConfigStore, McpServerConfigModel, ConfigScope, mask_secret, mask_url
from donovanagent.mcp.manager import McpManager
from donovanagent.mcp.security import McpRiskClassifier, McpTrustStore
from donovanagent.mcp.ui import (
    mcp_status_panel,
    mcp_tool_panel,
    mcp_resource_panel,
    mcp_prompt_panel,
    mcp_log_panel,
)
from donovanagent.utils.errors import DonovanAgentError

console = Console()

app = typer.Typer(
    name="DonovanAgent",
    help="Agentic AI System for your terminal ÃƒÂ¢Ã¢â€šÂ¬Ã¢â‚¬Â runs on macOS, Linux, and Windows.",
    invoke_without_command=True,
    no_args_is_help=False,
)
config_app = typer.Typer(help="Inspect and update configuration.")
model_app = typer.Typer(help="Inspect or change the active model/provider.", invoke_without_command=True)
tools_app = typer.Typer(help="List or toggle tools.", invoke_without_command=True)
permissions_app = typer.Typer(help="Manage approved DonovanAgent workspaces.", invoke_without_command=True)
activity_app = typer.Typer(help="Control activity stream.", invoke_without_command=True)
think_app = typer.Typer(help="Control thinking summaries.", invoke_without_command=True)
plan_app = typer.Typer(help="Plan mode for complex tasks.", invoke_without_command=True)
memory_app = typer.Typer(help="Persistent memory operations.", invoke_without_command=True)
context_app = typer.Typer(help="Project context.", invoke_without_command=True)
backend_app = typer.Typer(help="Execution backend management.", invoke_without_command=True)
browser_app = typer.Typer(help="Browser automation.", invoke_without_command=True)
checkpoint_app = typer.Typer(help="Checkpoint management.", invoke_without_command=True)
schedule_app = typer.Typer(help="Scheduled task management.", invoke_without_command=True)
subagents_app = typer.Typer(help="Subagent management.", invoke_without_command=True)
skill_app = typer.Typer(help="Skill management.", invoke_without_command=True)
mcp_app = typer.Typer(help="MCP server management.", invoke_without_command=True)

app.add_typer(config_app, name="config")
app.add_typer(model_app, name="model")
app.add_typer(tools_app, name="tools")
app.add_typer(permissions_app, name="permissions")
app.add_typer(activity_app, name="activity")
app.add_typer(think_app, name="think")
app.add_typer(plan_app, name="plan")
app.add_typer(memory_app, name="memory")
app.add_typer(context_app, name="context")
app.add_typer(backend_app, name="backend")
app.add_typer(browser_app, name="browser")
app.add_typer(checkpoint_app, name="checkpoint")
app.add_typer(schedule_app, name="schedule")
app.add_typer(subagents_app, name="subagents")
app.add_typer(skill_app, name="skill")
app.add_typer(mcp_app, name="mcp")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit."),
    yes: bool = typer.Option(False, "--yes", help="Approve non-high-risk operations automatically."),
) -> None:
    if version:
        console.print(f"DonovanAgent {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        try:
            DonovanAgentApp(assume_yes=yes, console=console).run_interactive()
        except DonovanAgentError as exc:
            console.print(error_panel(str(exc)))
            raise typer.Exit(1) from exc


@app.command()
def setup() -> None:
    """Run the first-time setup wizard."""
    manager = ConfigManager()
    run_setup_wizard(manager, console)
    run_doctor(manager, console)


@app.command()
def doctor() -> None:
    """Check the system environment and configured services."""
    ok = run_doctor(ConfigManager(), console)
    raise typer.Exit(0 if ok else 1)


@model_app.callback(invoke_without_command=True)
def model_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    manager = ConfigManager()
    config = manager.load(create=True)
    console.print(
        Panel(
            f"Provider: {config.provider.active}\n"
            f"Model: {config.provider.model or 'not configured'}\n"
            f"Base URL: {config.provider.base_url or 'not configured'}\n"
            f"API key env: {config.provider.api_key_env or '(none)'}",
            title="Model",
            border_style="white",
        )
    )


@model_app.command("set")
def model_set() -> None:
    """Change provider, base URL, API key env, and model."""
    manager = ConfigManager()
    config = manager.load(create=True)
    configure_model(manager, console, config)
    manager.save(config)
    console.print(info_panel("Model configuration updated."))


@config_app.command("show")
def config_show() -> None:
    """Show sanitized configuration."""
    manager = ConfigManager()
    config = manager.load(create=True)
    console.print(config_table(manager.sanitized(config)))


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set a dotted config key."""
    manager = ConfigManager()
    try:
        config = manager.set_value(key, value)
    except DonovanAgentError as exc:
        console.print(error_panel(str(exc)))
        raise typer.Exit(1) from exc
    MemoryDatabase(config.memory.database_path).add_config_event("config_set", {"key": key})
    console.print(info_panel(f"Set {key}."))


@tools_app.callback(invoke_without_command=True)
def tools_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    manager = ConfigManager()
    config = manager.load(create=True)
    console.print(tools_table(build_default_registry(config).rows()))


@tools_app.command("enable")
def tools_enable(tool_name: str) -> None:
    set_tool_enabled(tool_name, True)


@tools_app.command("disable")
def tools_disable(tool_name: str) -> None:
    set_tool_enabled(tool_name, False)


def set_tool_enabled(tool_name: str, enabled: bool) -> None:
    manager = ConfigManager()
    config = manager.load(create=True)
    registry = build_default_registry(config)
    if tool_name not in {tool.name for tool in registry.list()}:
        console.print(error_panel(f"Unknown tool: {tool_name}"))
        raise typer.Exit(1)
    key = registry.get(tool_name).enabled_key
    node = config.tools
    parts = key.split(".")
    for part in parts[:-1]:
        node = getattr(node, part)
    setattr(node, parts[-1], enabled)
    manager.save(config)
    MemoryDatabase(config.memory.database_path).add_config_event(
        "tool_toggle", {"tool": tool_name, "enabled": enabled}
    )
    console.print(info_panel(f"{'Enabled' if enabled else 'Disabled'} {tool_name}."))


@app.command()
def sessions(limit: int = typer.Option(50, help="Maximum sessions to show.")) -> None:
    """List saved sessions."""
    manager = ConfigManager()
    config = manager.load(create=True)
    db = MemoryDatabase(config.memory.database_path)
    db.initialize()
    console.print(sessions_table(db.list_sessions(limit=limit)))


@app.command()
def chat(prompt: str = typer.Argument(..., help="Prompt to send once."), yes: bool = False) -> None:
    """Run a one-shot prompt non-interactively."""
    run_agent_once(prompt, yes=yes)


@app.command()
def run(task: str = typer.Argument(..., help="Task for the agent to execute."), yes: bool = False) -> None:
    """Run an agent task in non-interactive mode."""
    run_agent_once(task, yes=yes)


def run_agent_once(prompt: str, *, yes: bool = False) -> None:
    try:
        pilot = DonovanAgentApp(console=console, assume_yes=yes)
        answer = pilot.one_shot(prompt)
    except DonovanAgentError as exc:
        console.print(error_panel(str(exc)))
        raise typer.Exit(1) from exc
    agent = pilot.ensure_agent()
    panel = tools_used_panel(agent.last_tool_names)
    if panel:
        console.print(panel)
    console.print(assistant_panel(answer))
    console.print(context_footer(agent.last_context_tokens, pilot.config.provider.context_window))


@app.command()
def skills(limit: int = typer.Option(50, help="Maximum skills to show.")) -> None:
    """List learned self-improvement skills."""
    manager = ConfigManager()
    config = manager.load(create=True)
    db = MemoryDatabase(config.memory.database_path)
    db.initialize()
    from donovanagent.app import skills_table

    console.print(skills_table(db.list_skills(limit=limit)))


@app.command()
def search(query: str = typer.Argument(..., help="Search query.")) -> None:
    """Run configured Tavily web search directly."""
    manager = ConfigManager()
    config = manager.load(create=True)
    if not config.search.enabled or config.search.provider != "tavily":
        console.print(
            error_panel(
                "Tavily is not configured. Run `DonovanAgent setup`, or set `search.enabled true` "
                "and place TAVILY_API_KEY in DonovanAgent's .env."
            )
        )
        raise typer.Exit(1)
    try:
        bundle = TavilySearchProvider(config.search).search(query, config.search.max_results)
    except DonovanAgentError as exc:
        console.print(error_panel(str(exc)))
        raise typer.Exit(1) from exc
    console.print(search_results_table(bundle.to_dict()))


@permissions_app.callback(invoke_without_command=True)
def permissions_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    manager = ConfigManager()
    config = manager.load(create=True)
    console.print(
        Panel(
            "Mode: "
            + config.app.permission_mode
            + "\n\nApproved paths:\n"
            + "\n".join(config.security.approved_paths),
            title="Permissions",
            border_style="white",
        )
    )


@permissions_app.command("add")
def permissions_add(path: str) -> None:
    manager = ConfigManager()
    config = manager.load(create=True)
    resolved = str(Path(path).expanduser().resolve(strict=False))
    if resolved not in config.security.approved_paths:
        config.security.approved_paths.append(resolved)
    manager.save(config)
    console.print(info_panel(f"Added approved path: {resolved}"))


@permissions_app.command("remove")
def permissions_remove(path: str) -> None:
    manager = ConfigManager()
    config = manager.load(create=True)
    resolved = str(Path(path).expanduser().resolve(strict=False))
    config.security.approved_paths = [
        item
        for item in config.security.approved_paths
        if str(Path(item).expanduser().resolve(strict=False)) != resolved
    ]
    manager.save(config)
    console.print(info_panel(f"Removed approved path: {resolved}"))


@app.command()
def update() -> None:
    """Show update instructions."""
    console.print(
        Panel(
            "DonovanAgent does not self-modify. To update a source checkout:\n\n"
            "git pull\n"
            "python -m pip install -e .\n\n"
            "If installed another way, reinstall using the same package manager.",
            title="Update",
            border_style="white",
        )
    )


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Helpers ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


def _get_app(*, assume_yes: bool = False) -> DonovanAgentApp:
    """Create a DonovanAgentApp and check configuration."""
    manager = ConfigManager()
    config = manager.load(create=True)
    if config.provider.active == "none":
        console.print(error_panel("DonovanAgent is not configured yet. Run `DonovanAgent setup` first."))
        raise typer.Exit(1)
    return DonovanAgentApp(manager=manager, console=console, assume_yes=assume_yes)


def _get_app_and_agent(*, assume_yes: bool = False) -> tuple[DonovanAgentApp, object]:
    """Create a DonovanAgentApp with an initialized agent."""
    pilot = _get_app(assume_yes=assume_yes)
    agent = pilot.ensure_agent()
    return pilot, agent


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Activity commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@activity_app.callback(invoke_without_command=True)
def activity_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    pilot = _get_app()
    cfg = pilot.config.activity_stream
    status = "on" if cfg.enabled else "off"
    mode = "compact" if cfg.compact else "verbose"
    console.print(info_panel(
        f"Activity stream: {status}\nMode: {mode}\n"
        f"Show timers: {cfg.show_timers}\n"
        f"Show results: {cfg.show_result_summaries}"
    ))


@activity_app.command("on")
def activity_on() -> None:
    """Enable activity stream."""
    pilot = _get_app()
    pilot.config.activity_stream.enabled = True
    pilot.manager.save(pilot.config)
    console.print(info_panel("Activity stream enabled."))


@activity_app.command("off")
def activity_off() -> None:
    """Disable activity stream."""
    pilot = _get_app()
    pilot.config.activity_stream.enabled = False
    pilot.manager.save(pilot.config)
    console.print(info_panel("Activity stream disabled."))


@activity_app.command("compact")
def activity_compact() -> None:
    """Set activity stream to compact mode."""
    pilot = _get_app()
    pilot.config.activity_stream.compact = True
    pilot.manager.save(pilot.config)
    console.print(info_panel("Activity stream set to compact mode."))


@activity_app.command("verbose")
def activity_verbose() -> None:
    """Set activity stream to verbose mode."""
    pilot = _get_app()
    pilot.config.activity_stream.compact = False
    pilot.manager.save(pilot.config)
    console.print(info_panel("Activity stream set to verbose mode."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Think commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@think_app.callback(invoke_without_command=True)
def think_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    pilot = _get_app()
    console.print(info_panel(
        f"Thinking: {'on' if pilot.config.thinking.enabled else 'off'}\n"
        f"Safe summaries: {pilot.config.thinking.show_safe_summaries}\n"
        f"Provider reasoning: {pilot.config.thinking.show_provider_reasoning_if_available}"
    ))


@think_app.command("on")
def think_on() -> None:
    """Enable thinking summaries."""
    pilot = _get_app()
    pilot.config.thinking.enabled = True
    pilot.manager.save(pilot.config)
    console.print(info_panel("Thinking summaries enabled."))


@think_app.command("off")
def think_off() -> None:
    """Disable thinking summaries."""
    pilot = _get_app()
    pilot.config.thinking.enabled = False
    pilot.manager.save(pilot.config)
    console.print(info_panel("Thinking summaries disabled."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Plan commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@plan_app.callback(invoke_without_command=True)
def plan_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    console.print(info_panel(
        "Plan mode:\n"
        "  DonovanAgent plan <task>       - Create a plan\n"
        "  DonovanAgent plan on           - Enable plan mode\n"
        "  DonovanAgent plan off          - Disable plan mode\n"
        "  DonovanAgent plan show         - Show current plan\n"
        "  DonovanAgent plan approve      - Approve the plan\n"
        "  DonovanAgent plan cancel       - Cancel the plan"
    ))


@plan_app.command("on")
def plan_on() -> None:
    """Enable plan mode for complex tasks."""
    pilot = _get_app()
    pilot.config.plan.default_for_complex_tasks = True
    pilot.manager.save(pilot.config)
    console.print(info_panel("Plan mode enabled for complex tasks."))


@plan_app.command("off")
def plan_off() -> None:
    """Disable plan mode."""
    pilot = _get_app()
    pilot.config.plan.default_for_complex_tasks = False
    pilot.manager.save(pilot.config)
    console.print(info_panel("Plan mode disabled."))


@plan_app.command("show")
def plan_show() -> None:
    """Show the current plan."""
    _, agent = _get_app_and_agent()
    plan = agent.plan_manager.current_plan
    if plan:
        items = "\n".join(
            f"  [{item.status}] {item.title}"
            for item in sorted(plan.items, key=lambda x: x.item_order)
        )
        console.print(info_panel(
            f"Task: {plan.task}\nStatus: {plan.status}\nItems:\n{items}"
        ))
    else:
        console.print(info_panel("No active plan."))


@plan_app.command("approve")
def plan_approve() -> None:
    """Approve and execute the current plan."""
    _, agent = _get_app_and_agent()
    if agent.plan_manager.current_plan:
        agent.plan_manager.approve()
        console.print(info_panel("Plan approved! Executing now..."))
    else:
        console.print(error_panel("No active plan to approve."))


@plan_app.command("cancel")
def plan_cancel() -> None:
    """Cancel the current plan."""
    _, agent = _get_app_and_agent()
    if agent.plan_manager.current_plan:
        agent.plan_manager.cancel()
        console.print(info_panel("Plan cancelled."))
    else:
        console.print(error_panel("No active plan to cancel."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Memory commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@memory_app.callback(invoke_without_command=True)
def memory_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    pilot = _get_app()
    cfg = pilot.config.memory
    console.print(info_panel(
        f"Memory enabled: {cfg.enabled}\n"
        f"Auto recall: {cfg.auto_recall}\n"
        f"Auto summarize: {cfg.auto_summarize_sessions}\n"
        f"Project context: {cfg.project_context_enabled}\n"
        f"Max recall items: {cfg.max_recall_items}"
    ))


@memory_app.command("search")
def memory_search(query: str = typer.Argument(..., help="Search query for memories.")) -> None:
    """Search persistent memories."""
    _, agent = _get_app_and_agent()
    results = agent.memory_manager.search(query)
    if results:
        for r in results:
            console.print(info_panel(
                f"[{r.get('memory_type', 'memory')}] {r.get('title', '')}\n"
                f"{r.get('summary', '')[:300]}",
                title=f"Memory #{r.get('id', '')}"
            ))
    else:
        console.print(info_panel("No memories found."))


@memory_app.command("add")
def memory_add(text: str = typer.Argument(..., help="Text to remember.")) -> None:
    """Add a memory."""
    _, agent = _get_app_and_agent()
    agent.memory_manager.add_memory(
        memory_type="user_preference",
        title="Manual entry",
        content=text,
        source="user",
    )
    console.print(info_panel("Memory saved."))


@memory_app.command("forget")
def memory_forget(memory_id: int = typer.Argument(..., help="Memory ID to delete.")) -> None:
    """Delete a memory by ID."""
    _, agent = _get_app_and_agent()
    agent.memory_manager.delete(memory_id)
    console.print(info_panel(f"Memory #{memory_id} deleted."))


@memory_app.command("summarize")
def memory_summarize() -> None:
    """Summarize the current session."""
    pilot, agent = _get_app_and_agent()
    session_id = pilot.start_session()
    from donovanagent.memory.summaries import generate_session_summary
    msgs = pilot.db.recent_messages(session_id, limit=24)
    summary = generate_session_summary(pilot.db, session_id, msgs)
    console.print(info_panel(summary, title="Session Summary"))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Context commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@context_app.callback(invoke_without_command=True)
def context_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    console.print(info_panel(
        "Context commands:\n"
        "  DonovanAgent context project   - Show project context\n"
        "  DonovanAgent context refresh   - Refresh project context"
    ))


@context_app.command("project")
def context_project() -> None:
    """Show detected project context."""
    pilot = _get_app()
    ws = pilot.config.app.default_workspace
    ctx = detect_project_context(ws)
    if any(ctx.values()):
        lines = [
            f"Language: {ctx.get('language', 'unknown')}",
            f"Package manager: {ctx.get('package_manager', 'unknown')}",
            f"Test commands: {', '.join(ctx.get('test_commands', []))}",
            f"Build commands: {', '.join(ctx.get('build_commands', []))}",
        ]
        console.print(info_panel("\n".join(lines), title="Project Context"))
    else:
        console.print(info_panel("No project context detected."))


@context_app.command("refresh")
def context_refresh() -> None:
    """Refresh detected project context."""
    _, agent = _get_app_and_agent()
    ws = agent.config.app.default_workspace
    agent.memory_manager.generate_project_context(ws)
    console.print(info_panel("Project context refreshed."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Backend commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@backend_app.callback(invoke_without_command=True)
def backend_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    pilot, agent = _get_app_and_agent()
    console.print(info_panel(
        f"Active backend: {agent.backend_manager.active_name}\n"
        "Available: local, docker, ssh\n"
        "Switch: DonovanAgent backend set local|docker|ssh"
    ))


@backend_app.command("set")
def backend_set(
    name: str = typer.Argument(..., help="Backend to switch to: local, docker, or ssh."),
) -> None:
    """Switch execution backend."""
    pilot, agent = _get_app_and_agent()
    try:
        backend_name = agent.backend_manager.switch(name)
        console.print(info_panel(f"Switched to backend: {backend_name}"))
    except Exception as exc:
        console.print(error_panel(str(exc)))
        raise typer.Exit(1) from exc


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Browser commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@browser_app.callback(invoke_without_command=True)
def browser_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    console.print(info_panel(
        "Browser commands:\n"
        "  DonovanAgent browser open <url>       - Open a URL\n"
        "  DonovanAgent browser close            - Close the browser\n"
        "  DonovanAgent browser screenshot       - Take a screenshot\n"
        "  DonovanAgent browser text             - Get page text\n"
        "  DonovanAgent browser url              - Get current URL\n"
        "  DonovanAgent browser back             - Navigate back\n"
        "  DonovanAgent browser reload           - Reload page"
    ))


@browser_app.command("open")
def browser_open(url: str = typer.Argument(..., help="URL to open.")) -> None:
    """Open a URL in the browser."""
    _, agent = _get_app_and_agent()
    try:
        agent.browser_service.open(url)
        console.print(info_panel(f"Opened: {url}"))
    except Exception as exc:
        console.print(error_panel(str(exc)))
        raise typer.Exit(1) from exc


@browser_app.command("close")
def browser_close() -> None:
    """Close the browser."""
    _, agent = _get_app_and_agent()
    agent.browser_service.close()
    console.print(info_panel("Browser closed."))


@browser_app.command("screenshot")
def browser_screenshot() -> None:
    """Take a browser screenshot."""
    _, agent = _get_app_and_agent()
    try:
        path = agent.browser_service.screenshot()
        console.print(info_panel(f"Screenshot saved to: {path}"))
    except Exception as exc:
        console.print(error_panel(str(exc)))
        raise typer.Exit(1) from exc


@browser_app.command("text")
def browser_text() -> None:
    """Get visible text from the current page."""
    _, agent = _get_app_and_agent()
    if agent.browser_service.is_open:
        console.print(agent.browser_service.get_text()[:2000])
    else:
        console.print(error_panel("Browser is not open."))


@browser_app.command("url")
def browser_url() -> None:
    """Get the current page URL."""
    _, agent = _get_app_and_agent()
    if agent.browser_service.is_open:
        console.print(info_panel(agent.browser_service.current_url()))
    else:
        console.print(error_panel("Browser is not open."))


@browser_app.command("back")
def browser_back() -> None:
    """Navigate back in history."""
    _, agent = _get_app_and_agent()
    if agent.browser_service.is_open:
        agent.browser_service.back()
        console.print(info_panel("Navigated back."))
    else:
        console.print(error_panel("Browser is not open."))


@browser_app.command("reload")
def browser_reload() -> None:
    """Reload the current page."""
    _, agent = _get_app_and_agent()
    if agent.browser_service.is_open:
        agent.browser_service.reload()
        console.print(info_panel("Page reloaded."))
    else:
        console.print(error_panel("Browser is not open."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Checkpoint commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@checkpoint_app.callback(invoke_without_command=True)
def checkpoint_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    console.print(info_panel(
        "Checkpoint commands:\n"
        "  DonovanAgent checkpoint list           - List checkpoints\n"
        "  DonovanAgent checkpoint show <id>      - Show checkpoint details\n"
        "  DonovanAgent checkpoint diff <id>      - Show diff before checkpoint\n"
        "  DonovanAgent checkpoint restore <id>   - Restore checkpoint\n"
        "  DonovanAgent checkpoint delete <id>    - Delete checkpoint"
    ))


@checkpoint_app.command("list")
def checkpoint_list() -> None:
    """List all checkpoints."""
    _, agent = _get_app_and_agent()
    cps = agent.checkpoints.list()
    if cps:
        for cp in cps[:10]:
            console.print(info_panel(
                f"ID: {cp.id}\n"
                f"Reason: {cp.reason}\n"
                f"Files: {len(cp.affected_paths)}\n"
                f"Created: {cp.created_at}",
                title="Checkpoint"
            ))
    else:
        console.print(info_panel("No checkpoints found."))


@checkpoint_app.command("show")
def checkpoint_show(checkpoint_id: str = typer.Argument(..., help="Checkpoint ID.")) -> None:
    """Show checkpoint details."""
    _, agent = _get_app_and_agent()
    cp = agent.checkpoints.get(checkpoint_id)
    if cp:
        console.print(info_panel(
            f"ID: {cp.id}\n"
            f"Reason: {cp.reason}\n"
            f"Tool: {cp.tool_name}\n"
            f"Affected: {', '.join(cp.affected_paths[:5])}\n"
            f"Created: {cp.created_at}\n"
            f"Restored: {cp.restored_at or 'never'}",
            title="Checkpoint Details"
        ))
    else:
        console.print(error_panel(f"Checkpoint not found: {checkpoint_id}"))


@checkpoint_app.command("diff")
def checkpoint_diff(checkpoint_id: str = typer.Argument(..., help="Checkpoint ID.")) -> None:
    """Show git diff before a checkpoint was created."""
    _, agent = _get_app_and_agent()
    diff = agent.checkpoints.diff(checkpoint_id)
    if diff:
        console.print(info_panel(diff[:2000], title="Git Diff Before"))
    else:
        console.print(info_panel("No diff available."))


@checkpoint_app.command("restore")
def checkpoint_restore(checkpoint_id: str = typer.Argument(..., help="Checkpoint ID to restore.")) -> None:
    """Restore files from a checkpoint."""
    _, agent = _get_app_and_agent()
    pre = agent.checkpoints.restore(checkpoint_id)
    if pre:
        console.print(info_panel(
            f"Restored checkpoint {checkpoint_id}.\n"
            f"A pre-restore checkpoint was created: {pre.id}"
        ))
    else:
        console.print(error_panel(f"Failed to restore: {checkpoint_id}"))


@checkpoint_app.command("delete")
def checkpoint_delete(checkpoint_id: str = typer.Argument(..., help="Checkpoint ID to delete.")) -> None:
    """Delete a checkpoint."""
    _, agent = _get_app_and_agent()
    agent.checkpoints.delete(checkpoint_id)
    console.print(info_panel(f"Checkpoint {checkpoint_id} deleted."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Schedule commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@schedule_app.callback(invoke_without_command=True)
def schedule_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    console.print(info_panel(
        "Schedule commands:\n"
        "  DonovanAgent schedule list            - List scheduled tasks\n"
        "  DonovanAgent schedule remove <id>     - Remove a scheduled task\n"
        "  DonovanAgent schedule pause <id>      - Pause a scheduled task\n"
        "  DonovanAgent schedule resume <id>     - Resume a scheduled task\n"
        "  DonovanAgent schedule run <id>        - Run a scheduled task now"
    ))


@schedule_app.command("list")
def schedule_list() -> None:
    """List scheduled tasks."""
    _, agent = _get_app_and_agent()
    tasks = agent.scheduler.list_tasks()
    if tasks:
        for t in tasks:
            console.print(info_panel(
                f"Name: {t.name}\n"
                f"Type: {t.schedule_type}\n"
                f"Enabled: {t.enabled}\n"
                f"Next run: {t.next_run_at}\n"
                f"Last status: {t.last_status}",
                title=f"Scheduled: {t.id}"
            ))
    else:
        console.print(info_panel("No scheduled tasks."))


@schedule_app.command("remove")
def schedule_remove(task_id: str = typer.Argument(..., help="Task ID to remove.")) -> None:
    """Remove a scheduled task."""
    _, agent = _get_app_and_agent()
    agent.scheduler.remove_task(task_id)
    console.print(info_panel(f"Task {task_id} removed."))


@schedule_app.command("pause")
def schedule_pause(task_id: str = typer.Argument(..., help="Task ID to pause.")) -> None:
    """Pause a scheduled task."""
    _, agent = _get_app_and_agent()
    agent.scheduler.pause_task(task_id)
    console.print(info_panel(f"Task {task_id} paused."))


@schedule_app.command("resume")
def schedule_resume(task_id: str = typer.Argument(..., help="Task ID to resume.")) -> None:
    """Resume a paused scheduled task."""
    _, agent = _get_app_and_agent()
    agent.scheduler.resume_task(task_id)
    console.print(info_panel(f"Task {task_id} resumed."))


@schedule_app.command("run")
def schedule_run(task_id: str = typer.Argument(..., help="Task ID to run now.")) -> None:
    """Run a scheduled task immediately."""
    _, agent = _get_app_and_agent()
    result = agent.scheduler.run_now(task_id)
    if result:
        console.print(info_panel(result[:500], title="Scheduled Run"))
    else:
        console.print(error_panel(f"Task not found: {task_id}"))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Subagents commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@subagents_app.callback(invoke_without_command=True)
def subagents_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _, agent = _get_app_and_agent()
    subs = agent.subagent_manager.list()
    if subs:
        for s in subs:
            console.print(info_panel(
                f"ID: {s.id}\n"
                f"Role: {s.role}\n"
                f"Status: {s.status}\n"
                f"Tools: {', '.join(s.allowed_tools[:5])}",
                title=f"Subagent: {s.name}"
            ))
    else:
        console.print(info_panel("No subagents."))


@subagents_app.command("on")
def subagents_on() -> None:
    """Enable subagents."""
    pilot = _get_app()
    pilot.config.subagents.enabled = True
    pilot.manager.save(pilot.config)
    console.print(info_panel("Subagents enabled."))


@subagents_app.command("off")
def subagents_off() -> None:
    """Disable subagents."""
    pilot = _get_app()
    pilot.config.subagents.enabled = False
    pilot.manager.save(pilot.config)
    console.print(info_panel("Subagents disabled."))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Skill commands ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


@skill_app.callback(invoke_without_command=True)
def skill_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    console.print(info_panel(
        "Skill commands:\n"
        "  DonovanAgent skill list               - List all skills\n"
        "  DonovanAgent skill search <query>     - Search skills\n"
        "  DonovanAgent skill show <name>        - Show skill details\n"
        "  DonovanAgent skill delete <name>      - Delete a skill\n"
        "  DonovanAgent skill drafts             - List draft skills\n"
        "  DonovanAgent skill approve <name>     - Promote a draft to learned\n"
        "  DonovanAgent skill reject <name>      - Delete a draft skill\n"
        "  DonovanAgent skill add <name>         - Add a new skill"
    ))


@skill_app.command("list")
def skill_list() -> None:
    """List all skills."""
    _, agent = _get_app_and_agent()
    skills = agent.skill_manager.list_all()
    if skills:
        from rich.table import Table
        from rich import box
        table = Table(title="Skills", box=box.SIMPLE_HEAVY)
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Confidence")
        table.add_column("Uses")
        for s in skills:
            table.add_row(
                s.name, s.skill_type.value,
                f"{s.confidence:.2f}", str(s.usage_count),
            )
        console.print(table)
    else:
        console.print(info_panel("No skills found."))


@skill_app.command("search")
def skill_search(query: str = typer.Argument(..., help="Search query.")) -> None:
    """Search skills by name, trigger, or content."""
    _, agent = _get_app_and_agent()
    results = agent.skill_manager.search(query)
    if results:
        for s in results:
            console.print(info_panel(
                f"{s.name} ({s.skill_type.value}, confidence: {s.confidence})"
            ))
    else:
        console.print(info_panel("No matching skills."))


@skill_app.command("show")
def skill_show(name: str = typer.Argument(..., help="Skill name.")) -> None:
    """Show skill details and content."""
    _, agent = _get_app_and_agent()
    skills = agent.skill_manager.load_all()
    for s in skills:
        if s.name == name:
            console.print(info_panel(
                f"{s.content[:2000]}\n\n"
                f"Type: {s.skill_type.value}\n"
                f"Confidence: {s.confidence}\n"
                f"Uses: {s.usage_count}\n"
                f"Triggers: {', '.join(s.triggers[:5])}",
                title=s.name
            ))
            return
    console.print(error_panel(f"Skill not found: {name}"))


@skill_app.command("delete")
def skill_delete(name: str = typer.Argument(..., help="Skill name to delete.")) -> None:
    """Delete a skill."""
    _, agent = _get_app_and_agent()
    if agent.skill_manager.delete_skill(name):
        console.print(info_panel(f"Skill '{name}' deleted."))
    else:
        console.print(error_panel(f"Skill not found: {name}"))


@skill_app.command("drafts")
def skill_drafts() -> None:
    """List draft skills pending approval."""
    _, agent = _get_app_and_agent()
    drafts = agent.skill_manager.list_drafts()
    if drafts:
        for s in drafts:
            console.print(info_panel(
                f"{s.name} (confidence: {s.confidence})"
            ))
    else:
        console.print(info_panel("No draft skills."))


@skill_app.command("approve")
def skill_approve(name: str = typer.Argument(..., help="Draft skill name to approve.")) -> None:
    """Promote a draft skill to learned."""
    _, agent = _get_app_and_agent()
    if agent.skill_manager.promote_draft(name):
        console.print(info_panel(f"Skill '{name}' promoted from draft to learned."))
    else:
        console.print(error_panel(f"Draft not found: {name}"))


@skill_app.command("reject")
def skill_reject(name: str = typer.Argument(..., help="Draft skill name to reject.")) -> None:
    """Delete a draft skill."""
    _, agent = _get_app_and_agent()
    if agent.skill_manager.delete_skill(name):
        console.print(info_panel(f"Draft '{name}' rejected and deleted."))
    else:
        console.print(error_panel(f"Draft not found: {name}"))


@skill_app.command("add")
def skill_add(
    name: str = typer.Argument(..., help="Skill name."),
    content: str = typer.Option("", "--content", "-c", help="Skill content as text."),
) -> None:
    """Add a new skill."""
    if content:
        _, agent = _get_app_and_agent()
        from donovanagent.skills.models import Skill, SkillType
        skill = Skill(
            name=name,
            title=name.replace("_", " ").title(),
            description="",
            content=content,
            skill_type=SkillType.USER,
        )
        agent.skill_manager.save_file(skill)
        console.print(info_panel(f"Skill '{name}' saved."))
    else:
        from donovanagent.app import _skill_dir
        pilot = _get_app()
        skill_path = _skill_dir(pilot.config.app.default_workspace) / f"{name}.md"
        if skill_path.exists():
            from rich.prompt import Confirm as ConfirmAsk
            overwrite = ConfirmAsk.ask(f"Skill '{name}' already exists. Overwrite?", default=False)
            if not overwrite:
                return
        console.print(info_panel(
            f"Paste instructions for skill '{name}' below and press Enter twice when done."
        ))
        lines: list[str] = []
        try:
            from prompt_toolkit import prompt as pt_prompt
            while True:
                line = pt_prompt("  ")
                if not line and (not lines or not lines[-1]):
                    break
                lines.append(line)
        except (KeyboardInterrupt, EOFError):
            console.print("[dim]Cancelled.[/dim]")
            return
        body = "\n".join(lines).strip()
        if not body:
            console.print(error_panel("No content provided."))
            return
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(body + "\n", encoding="utf-8")
        console.print(info_panel(f"Skill '{name}' saved ({len(body)} chars)"))


# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Main ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


# =============================================================================
# MCP commands
# =============================================================================


def _get_mcp_manager() -> McpManager:
    """Create a McpManager for the current config."""
    from donovanagent.config.manager import ConfigManager
    mgr = ConfigManager()
    config = mgr.load(create=True)
    registry = build_default_registry(config)
    return McpManager(config, registry, paths=mgr.paths)


@mcp_app.callback(invoke_without_command=True)
def mcp_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    manager = _get_mcp_manager()
    statuses = manager.list_statuses()
    console.print(mcp_status_panel(statuses))


@mcp_app.command("list")
def mcp_list() -> None:
    """List all configured MCP servers."""
    manager = _get_mcp_manager()
    statuses = manager.list_statuses()
    console.print(mcp_status_panel(statuses))


@mcp_app.command("get")
def mcp_get(name: str = typer.Argument(..., help="Server name.")) -> None:
    """Show detailed info about an MCP server."""
    config_model, scope = _get_mcp_manager().config_store.load_server(name)
    if config_model is None:
        console.print(error_panel(f"MCP server '{name}' not found."))
        raise typer.Exit(1)

    status = _get_mcp_manager().get_server_status(name)
    lines = [
        f"Name: {name}",
        f"Type: {config_model.type}",
        f"Scope: {scope}",
        f"Enabled: {config_model.enabled}",
        f"Trust: {config_model.trust}",
        f"Timeout: {config_model.timeout_ms}ms",
        f"Max Output: {config_model.max_output_tokens} tokens",
    ]
    if config_model.description:
        lines.append(f"Description: {config_model.description}")

    if config_model.type == "stdio":
        lines.append(f"Command: {config_model.command}")
        if config_model.args:
            lines.append(f"Args: {' '.join(config_model.args)}")
        env_display = config_model.get_display_env()
        if env_display:
            lines.append(f"Env: {env_display}")
    elif config_model.type in ("http", "sse"):
        lines.append(f"URL: {mask_url(config_model.url)}")
        headers_display = config_model.get_display_headers()
        if headers_display:
            lines.append(f"Headers: {headers_display}")

    if status:
        lines.append(f"Connected: {status.connected}")
        lines.append(f"Tools: {status.tool_count}")
        lines.append(f"Resources: {status.resource_count}")
        lines.append(f"Prompts: {status.prompt_count}")
        if status.last_error:
            lines.append(f"Last Error: {status.last_error}")

    console.print(info_panel("\n".join(lines), title=f"MCP Server: {name}"))


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="Server name."),
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport type: stdio, http, streamable-http, sse."),
    scope: str = typer.Option("project", "--scope", "-s", help="Config scope: user, project, local."),
    env: list[str] = typer.Option([], "--env", "-e", help="Environment variables (KEY=VALUE)."),
    header: list[str] = typer.Option([], "--header", "-h", help="HTTP headers (Name: value)."),
    timeout_ms: int = typer.Option(60000, "--timeout-ms", help="Request timeout in milliseconds."),
    max_output_tokens: int = typer.Option(25000, "--max-output-tokens", help="Max output token limit."),
    args: list[str] = typer.Argument(None, help="Server command and args (after --)."),
) -> None:
    """Add an MCP server.

    Examples:

    donovan mcp add --transport http notion https://mcp.notion.com/mcp

    donovan mcp add --transport stdio filesystem -- npx -y @modelcontextprotocol/server-filesystem .

    donovan mcp add --env AIRTABLE_API_KEY=${AIRTABLE_API_KEY} airtable -- npx -y airtable-mcp-server
    """
    if not name.strip():
        console.print(error_panel("Server name is required."))
        raise typer.Exit(1)

    valid_scopes = ("user", "project", "local")
    if scope not in valid_scopes:
        console.print(error_panel(f"Invalid scope: {scope}. Valid: {', '.join(valid_scopes)}"))
        raise typer.Exit(1)

    valid_transports = ("stdio", "http", "streamable-http", "sse")
    if transport not in valid_transports:
        console.print(error_panel(f"Invalid transport: {transport}. Valid: {', '.join(valid_transports)}"))
        raise typer.Exit(1)

    # Parse env vars
    parsed_env: dict[str, str] = {}
    for e in env:
        if "=" not in e:
            console.print(error_panel(f"Invalid env format: {e}. Use KEY=VALUE."))
            raise typer.Exit(1)
        key, value = e.split("=", 1)
        parsed_env[key] = value

    # Parse headers
    parsed_headers: dict[str, str] = {}
    for h in header:
        if ":" not in h:
            console.print(error_panel(f"Invalid header format: {h}. Use 'Name: value'."))
            raise typer.Exit(1)
        key, value = h.split(":", 1)
        parsed_headers[key.strip()] = value.strip()

    # Build config
    server_config: dict[str, Any] = {
        "type": transport,
        "enabled": True,
        "trust": "ask",
        "timeout_ms": timeout_ms,
        "max_output_tokens": max_output_tokens,
    }

    if transport == "stdio":
        if not args:
            console.print(error_panel("For stdio transport, provide command after -- separator."))
            raise typer.Exit(1)
        server_config["command"] = args[0]
        server_config["args"] = args[1:]
        server_config["env"] = parsed_env
    elif transport in ("http", "streamable-http", "sse"):
        if not args:
            console.print(error_panel("URL required for HTTP/SSE transport."))
            raise typer.Exit(1)
        server_config["url"] = args[0]
        server_config["headers"] = parsed_headers

    # Validate and save
    try:
        model = McpServerConfigModel(**server_config)
    except Exception as exc:
        console.print(error_panel(f"Invalid server config: {exc}"))
        raise typer.Exit(1) from exc

    config_scope: ConfigScope = scope  # type: ignore
    store = _get_mcp_manager().config_store
    store.save_server(name, model, config_scope)

    console.print(info_panel(
        f"MCP server '{name}' added ({transport}, scope: {scope}).\n"
        f"Use 'donovan mcp connect {name}' to connect."
    ))


@mcp_app.command("add-json")
def mcp_add_json(
    name: str = typer.Argument(..., help="Server name."),
    json_config: str = typer.Argument(..., help="JSON config string."),
    scope: str = typer.Option("project", "--scope", "-s", help="Config scope: user, project, local."),
) -> None:
    """Add an MCP server from a JSON config string."""
    import json as _json
    try:
        data = _json.loads(json_config)
    except _json.JSONDecodeError as exc:
        console.print(error_panel(f"Invalid JSON: {exc}"))
        raise typer.Exit(1) from exc

    try:
        model = McpServerConfigModel(**data)
    except Exception as exc:
        console.print(error_panel(f"Invalid server config: {exc}"))
        raise typer.Exit(1) from exc

    config_scope: ConfigScope = scope  # type: ignore
    store = _get_mcp_manager().config_store
    store.save_server(name, model, config_scope)
    console.print(info_panel(f"MCP server '{name}' added from JSON (scope: {scope})."))


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="Server name."),
    scope: str | None = typer.Option(None, "--scope", "-s", help="Config scope: user, project, local."),
) -> None:
    """Remove an MCP server configuration."""
    store = _get_mcp_manager().config_store
    config_scope: ConfigScope | None = scope  # type: ignore
    if store.remove_server(name, config_scope):
        console.print(info_panel(f"MCP server '{name}' removed."))
    else:
        console.print(error_panel(f"MCP server '{name}' not found."))
        raise typer.Exit(1)


@mcp_app.command("enable")
def mcp_enable(name: str = typer.Argument(..., help="Server name.")) -> None:
    """Enable an MCP server."""
    _set_mcp_enabled(name, True)


@mcp_app.command("disable")
def mcp_disable(name: str = typer.Argument(..., help="Server name.")) -> None:
    """Disable an MCP server."""
    _set_mcp_enabled(name, False)


def _set_mcp_enabled(name: str, enabled: bool) -> None:
    manager = _get_mcp_manager()
    config_model, scope = manager.config_store.load_server(name)
    if config_model is None:
        console.print(error_panel(f"MCP server '{name}' not found."))
        raise typer.Exit(1)
    config_model.enabled = enabled
    manager.config_store.save_server(name, config_model, scope)
    state = "enabled" if enabled else "disabled"
    console.print(info_panel(f"MCP server '{name}' {state}."))


@mcp_app.command("trust")
def mcp_trust(
    name: str = typer.Argument(..., help="Server name."),
    scope: str = typer.Option("project", "--scope", "-s", help="Config scope: user, project."),
) -> None:
    """Trust an MCP server (allows it to run)."""
    manager = _get_mcp_manager()
    config_model, config_scope = manager.config_store.load_server(name)
    if config_model is None:
        console.print(error_panel(f"MCP server '{name}' not found."))
        raise typer.Exit(1)

    config_hash = config_model.trust_hash()
    trust_scope: ConfigScope = scope  # type: ignore
    manager.trust_store.set_trust(name, "trusted", trust_scope, config_hash)

    # Also update the config's trust field
    config_model.trust = "trusted"
    manager.config_store.save_server(name, config_model, config_scope)

    console.print(info_panel(f"MCP server '{name}' trusted (scope: {scope})."))


@mcp_app.command("block")
def mcp_block(
    name: str = typer.Argument(..., help="Server name."),
    scope: str = typer.Option("project", "--scope", "-s", help="Config scope: user, project."),
) -> None:
    """Block an MCP server (prevents it from running)."""
    manager = _get_mcp_manager()
    config_model, config_scope = manager.config_store.load_server(name)
    if config_model is None:
        console.print(error_panel(f"MCP server '{name}' not found."))
        raise typer.Exit(1)

    config_hash = config_model.trust_hash()
    trust_scope: ConfigScope = scope  # type: ignore
    manager.trust_store.set_trust(name, "blocked", trust_scope, config_hash)

    config_model.trust = "blocked"
    manager.config_store.save_server(name, config_model, config_scope)

    # Disconnect if running
    if name in manager.connected_servers:
        manager.disconnect_server(name)

    console.print(info_panel(f"MCP server '{name}' blocked (scope: {scope})."))


@mcp_app.command("reset-project-choices")
def mcp_reset_project_choices() -> None:
    """Reset all project-level MCP trust decisions."""
    manager = _get_mcp_manager()
    manager.trust_store.reset_project_choices()
    console.print(info_panel("Project MCP trust choices reset."))


@mcp_app.command("doctor")
def mcp_doctor(
    connect: bool = typer.Option(False, "--connect", help="Try connecting to validate servers."),
) -> None:
    """Check MCP configuration and diagnose issues."""
    manager = _get_mcp_manager()
    issues: list[str] = []
    ok_count = 0

    servers = manager.list_statuses()
    if not servers:
        console.print(info_panel("No MCP servers configured. Use 'donovan mcp add' to add one."))
        return

    for s in servers:
        config_model, scope = manager.config_store.load_server(s.name)

        # Check for duplicate names
        dupes = [x for x in servers if x.name == s.name and x.scope != s.scope]
        if dupes:
            issues.append(f"{s.name}: exists in multiple scopes: {s.scope}, {dupes[0].scope}")

        # Check missing env vars
        if config_model:
            for key in config_model.env:
                if "${" in config_model.env[key]:
                    var_name = config_model.env[key].strip("${}")
                    if var_name not in config_model.resolve_env():
                        issues.append(f"{s.name}: env var '{var_name}' may not be set")

        # Check command exists (stdio)
        if s.type == "stdio" and s.command:
            import shutil
            cmd = s.command
            if cmd in ("npx", "npm", "yarn", "pnpm"):
                ok_count += 1  # these are standard tools
            elif not shutil.which(cmd):
                issues.append(f"{s.name}: command '{cmd}' not found in PATH")

        # Check URL validity (HTTP/SSE)
        if s.type in ("http", "sse") and s.url:
            if not s.url.startswith(("http://", "https://")):
                issues.append(f"{s.name}: URL '{s.url}' does not start with http:// or https://")
            else:
                # Check that HTTP transports will send the required Accept header
                if s.type == "http":
                    from donovanagent.mcp.transport_http import _MCP_REQUIRED_HEADERS
                    expected_accept = _MCP_REQUIRED_HEADERS.get("Accept", "")
                    if "text/event-stream" not in expected_accept or "application/json" not in expected_accept:
                        issues.append(
                            f"{s.name}: HTTP transport missing required Accept header. "
                            f"Expected: Accept: application/json, text/event-stream"
                        )

        if config_model and config_model.trust == "blocked":
            issues.append(f"{s.name}: server is blocked")

        # Try connecting if --connect
        if connect and s.enabled and not s.trust == "blocked":
            try:
                result = manager.connect_server(s.name)
                ok_count += 1
            except Exception as exc:
                issues.append(f"{s.name}: connection failed: {exc}")

    if issues:
        for issue in issues:
            console.print(error_panel(issue))
        raise typer.Exit(1)
    else:
        console.print(info_panel(f"All {len(servers)} MCP server(s) look good."))


def main(argv: Optional[list[str]] = None) -> None:
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    app()


if __name__ == "__main__":
    main()
