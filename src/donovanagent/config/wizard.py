from __future__ import annotations

from pathlib import Path

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from donovanagent.config.manager import ConfigManager
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.memory.database import MemoryDatabase
from donovanagent.providers.ollama import parse_ollama_tags
from donovanagent.ui.render import info_panel


def run_setup_wizard(manager: ConfigManager, console: Console, *, launch_note: bool = False) -> DonovanAgentConfig:
    config = manager.load(create=True)
    console.print(
        Panel(
            "[bold]Welcome to DonovanAgent[/bold]\n\n"
            "DonovanAgent is an Agentic AI System. It can read files, write files, "
            "run commands, and call configured APIs only within the permissions you grant.\n\n"
            "Safe defaults are enabled: writes, command execution, and code execution ask for approval.",
            title="Setup",
            border_style="white",
        )
    )
    configure_model(manager, console, config)
    configure_search(manager, console, config)
    configure_workspace(console, config)
    configure_tools(console, config)
    config.app.first_run_complete = True
    manager.save(config)
    MemoryDatabase(config.memory.database_path).initialize()
    manager.load(create=True)
    console.print(info_panel("Setup complete. Run `DonovanAgent doctor` any time to verify your environment."))
    if launch_note:
        console.print("[dim]Launching DonovanAgent...[/dim]")
    return config


def configure_model(manager: ConfigManager, console: Console, config: DonovanAgentConfig) -> None:
    choice = Prompt.ask(
        "Choose provider",
        choices=["openai", "anthropic", "deepseek", "qwen", "lmstudio", "custom", "ollama", "skip"],
        default="ollama",
    )
    if choice == "skip":
        config.provider.active = "none"
        return
    if choice == "anthropic":
        config.provider.active = "anthropic"
        api_key = Prompt.ask("Anthropic API key", password=True, default="")
        if api_key:
            manager.write_secret(config.providers.anthropic.api_key_env, api_key)
        config.providers.anthropic.model = Prompt.ask("Model", default=config.providers.anthropic.model or "anthropic-default")
        sync_active_provider(config)
        return
    if choice == "deepseek":
        config.provider.active = "deepseek"
        api_key = Prompt.ask("DeepSeek API key", password=True, default="")
        if api_key:
            manager.write_secret(config.providers.deepseek.api_key_env, api_key)
        config.providers.deepseek.model = Prompt.ask("Model", default=config.providers.deepseek.model or "deepseek-chat")
        sync_active_provider(config)
        return
    if choice == "qwen":
        config.provider.active = "qwen"
        api_key = Prompt.ask("DashScope API key", password=True, default="")
        if api_key:
            manager.write_secret(config.providers.qwen.api_key_env, api_key)
        config.providers.qwen.model = Prompt.ask("Model", default=config.providers.qwen.model or "qwen-max")
        sync_active_provider(config)
        return
    if choice == "lmstudio":
        config.provider.active = "lmstudio"
        config.providers.lmstudio.base_url = Prompt.ask("LM Studio base URL", default=config.providers.lmstudio.base_url)
        config.providers.lmstudio.model = Prompt.ask("Model name (leave blank to auto-detect)", default=config.providers.lmstudio.model or "")
        sync_active_provider(config)
        return
    if choice == "openai":
        config.provider.active = "openai"
        api_key = Prompt.ask("OpenAI API key", password=True, default="")
        if api_key:
            manager.write_secret(config.providers.openai.api_key_env, api_key)
        config.providers.openai.model = Prompt.ask("Model", default=config.providers.openai.model or "gpt-4.1")
        config.providers.openai.base_url = Prompt.ask(
            "Base URL", default=config.providers.openai.base_url or "https://api.openai.com/v1"
        )
    elif choice == "custom":
        config.provider.active = "openai_compatible"
        config.providers.custom.base_url = Prompt.ask(
            "OpenAI-compatible base URL",
            default=config.providers.custom.base_url or "http://127.0.0.1:1234/v1",
        )
        env_name = Prompt.ask("API key env var", default=config.providers.custom.api_key_env)
        config.providers.custom.api_key_env = env_name
        api_key = Prompt.ask("API key value (optional)", password=True, default="")
        if api_key:
            manager.write_secret(env_name, api_key)
        config.providers.custom.model = Prompt.ask("Model name", default=config.providers.custom.model or "")
    elif choice == "ollama":
        config.provider.active = "ollama"
        config.providers.ollama.base_url = Prompt.ask(
            "Ollama OpenAI-compatible URL", default=config.providers.ollama.base_url
        )
        config.providers.ollama.native_url = Prompt.ask(
            "Ollama native URL", default=config.providers.ollama.native_url
        )
        models = query_ollama_models(config.providers.ollama.native_url)
        if models:
            console.print("Installed Ollama models:")
            for model in models:
                console.print(f"  - {model}")
        config.providers.ollama.model = Prompt.ask(
            "Model name", default=config.providers.ollama.model or (models[0] if models else "")
        )
    sync_active_provider(config)


def sync_active_provider(config: DonovanAgentConfig) -> None:
    _map = {
        "openai": config.providers.openai,
        "openai_compatible": config.providers.custom,
        "anthropic": config.providers.anthropic,
        "deepseek": config.providers.deepseek,
        "lmstudio": config.providers.lmstudio,
        "qwen": config.providers.qwen,
    }
    src = _map.get(config.provider.active)
    if src is not None:
        config.provider.base_url = src.base_url
        config.provider.api_key_env = src.api_key_env
        config.provider.model = src.model
    elif config.provider.active == "ollama":
        config.provider.base_url = config.providers.ollama.base_url
        config.provider.api_key_env = config.providers.ollama.api_key_env
        config.provider.model = config.providers.ollama.model
    else:
        config.provider.base_url = ""
        config.provider.api_key_env = ""
        config.provider.model = ""


def query_ollama_models(native_url: str) -> list[str]:
    try:
        response = httpx.get(native_url.rstrip("/") + "/api/tags", timeout=3)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    return parse_ollama_tags(response.json())


def configure_search(manager: ConfigManager, console: Console, config: DonovanAgentConfig) -> None:
    enable = Confirm.ask("Do you want to enable web search with Tavily?", default=False)
    config.search.enabled = enable
    config.search.provider = "tavily" if enable else "none"
    config.tools.web_search.enabled = enable
    if not enable:
        return
    env_name = Prompt.ask("Tavily API key env var", default=config.search.tavily_api_key_env)
    config.search.tavily_api_key_env = env_name
    api_key = Prompt.ask("Tavily API key", password=True, default="")
    if api_key:
        manager.write_secret(env_name, api_key)
    config.search.search_depth = Prompt.ask(
        "Search depth", choices=["basic", "advanced"], default=config.search.search_depth
    )
    config.search.max_results = IntPrompt.ask("Default max results", default=config.search.max_results)


def configure_workspace(console: Console, config: DonovanAgentConfig) -> None:
    workspace = Prompt.ask("Default workspace folder", default=str(Path.cwd()))
    resolved = Path(workspace).expanduser().resolve(strict=False)
    config.app.default_workspace = str(resolved)
    config.security.approved_paths = [str(resolved)]
    console.print(
        Panel(
            "Permission modes:\n"
            "readonly  - read approved files only\n"
            "review    - propose and ask before writes, shell, and code\n"
            "workspace - writes inside approved workspace with approval\n"
            "full_autonomy - fewer prompts, destructive actions still require confirmation",
            title="Permissions",
            border_style="white",
        )
    )
    config.app.permission_mode = Prompt.ask(
        "Permission mode", choices=["readonly", "review", "workspace", "full_autonomy"], default="review"
    )
    if config.app.permission_mode == "full_autonomy":
        console.print("[bold red]Full autonomy mode still blocks or confirms destructive system actions.[/bold red]")


def configure_tools(console: Console, config: DonovanAgentConfig) -> None:
    console.print("Configure tools. Safe defaults are recommended.")
    config.tools.filesystem.enabled = Confirm.ask("Enable filesystem tools?", default=True)
    config.tools.filesystem.require_approval_for_write = Confirm.ask(
        "Require approval for file writes and patches?", default=True
    )
    config.tools.terminal.enabled = Confirm.ask("Enable terminal command tool?", default=True)
    config.tools.terminal.require_approval = Confirm.ask(
        "Require approval for terminal commands?", default=True
    )
    config.tools.code_execution.enabled = Confirm.ask("Enable Python code execution tool?", default=True)
    config.tools.code_execution.require_approval = Confirm.ask(
        "Require approval for Python code execution?", default=True
    )
    config.tools.system_info.enabled = True
