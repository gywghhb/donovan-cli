from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Theme = Literal["dark", "light", "system"]
PermissionMode = Literal["readonly", "review", "workspace", "full_autonomy"]
ProviderName = Literal["none", "openai", "openai_compatible", "ollama", "anthropic", "deepseek", "lmstudio", "qwen"]
SearchProviderName = Literal["tavily", "brave", "serper", "exa", "none"]
SearchDepth = Literal["basic", "advanced"]
ExecutionBackend = Literal["local", "docker", "ssh"]
ToolArgsDisplay = Literal["preview", "full", "none"]


class AppConfig(BaseModel):
    first_run_complete: bool = False
    theme: Theme = "dark"
    telemetry: bool = False
    default_workspace: str = Field(default_factory=lambda: str(Path.cwd()))
    permission_mode: PermissionMode = "review"
    strip_markdown_final: bool = True

    @field_validator("permission_mode", mode="before")
    @classmethod
    def migrate_unsafe(cls, value: str) -> str:
        if value == "unsafe":
            return "full_autonomy"
        return value


class ActiveProviderConfig(BaseModel):
    active: ProviderName = "none"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    temperature: float = 0.2
    max_tokens: int = 8192
    context_window: int = 256000
    timeout_seconds: int = 60
    stream: bool = True

    @field_validator("temperature")
    @classmethod
    def temperature_range(cls, value: float) -> float:
        if not 0 <= value <= 2:
            raise ValueError("temperature must be between 0 and 2")
        return value

    @field_validator("timeout_seconds", "max_tokens", "context_window")
    @classmethod
    def positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be positive")
        return value


class NamedProviderConfig(BaseModel):
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""


class OllamaConfig(BaseModel):
    base_url: str = "http://127.0.0.1:11434/v1"
    native_url: str = "http://127.0.0.1:11434"
    model: str = ""
    api_key_env: str = ""


class ProvidersConfig(BaseModel):
    openai: NamedProviderConfig = Field(
        default_factory=lambda: NamedProviderConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            model="gpt-4.1",
        )
    )
    custom: NamedProviderConfig = Field(
        default_factory=lambda: NamedProviderConfig(
            base_url="",
            api_key_env="DonovanAgent_API_KEY",
            model="",
        )
    )
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    anthropic: NamedProviderConfig = Field(
        default_factory=lambda: NamedProviderConfig(
            base_url="https://api.anthropic.com/v1",
            api_key_env="ANTHROPIC_API_KEY",
            model="anthropic-default",
        )
    )
    deepseek: NamedProviderConfig = Field(
        default_factory=lambda: NamedProviderConfig(
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            model="deepseek-chat",
        )
    )
    lmstudio: NamedProviderConfig = Field(
        default_factory=lambda: NamedProviderConfig(
            base_url="http://localhost:1234/v1",
            api_key_env="",
            model="",
        )
    )
    qwen: NamedProviderConfig = Field(
        default_factory=lambda: NamedProviderConfig(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="DASHSCOPE_API_KEY",
            model="qwen-max",
        )
    )


class SearchConfig(BaseModel):
    enabled: bool = False
    provider: SearchProviderName = "none"
    tavily_api_key_env: str = "TAVILY_API_KEY"
    max_results: int = 5
    search_depth: SearchDepth = "basic"
    include_answer: bool = True
    include_raw_content: bool = False

    @field_validator("max_results")
    @classmethod
    def max_results_range(cls, value: int) -> int:
        if not 1 <= value <= 20:
            raise ValueError("max_results must be between 1 and 20")
        return value


class TerminalToolConfig(BaseModel):
    enabled: bool = True
    require_approval: bool = True
    timeout_seconds: int = 120


class FilesystemToolConfig(BaseModel):
    enabled: bool = True
    require_approval_for_write: bool = True
    max_read_bytes: int = 300_000


class WebSearchToolConfig(BaseModel):
    enabled: bool = False


class CodeExecutionToolConfig(BaseModel):
    enabled: bool = True
    require_approval: bool = True
    timeout_seconds: int = 60


class BrowserToolConfig(BaseModel):
    enabled: bool = True


class SubagentsToolConfig(BaseModel):
    enabled: bool = True


class McpToolConfig(BaseModel):
    enabled: bool = True


class ToolsConfig(BaseModel):
    terminal: TerminalToolConfig = Field(default_factory=TerminalToolConfig)
    filesystem: FilesystemToolConfig = Field(default_factory=FilesystemToolConfig)
    web_search: WebSearchToolConfig = Field(default_factory=WebSearchToolConfig)
    system_info: WebSearchToolConfig = Field(default_factory=lambda: WebSearchToolConfig(enabled=True))
    code_execution: CodeExecutionToolConfig = Field(default_factory=CodeExecutionToolConfig)
    subagents: SubagentsToolConfig = Field(default_factory=lambda: SubagentsToolConfig(enabled=True))
    browser_tools: BrowserToolConfig = Field(default_factory=BrowserToolConfig)
    mcp_tools: McpToolConfig = Field(default_factory=lambda: McpToolConfig(enabled=True))


class SecurityConfig(BaseModel):
    approved_paths: list[str] = Field(default_factory=lambda: [str(Path.cwd())])
    blocked_paths: list[str] = Field(default_factory=list)
    allow_network: bool = True
    require_approval_for_destructive_commands: bool = True
    require_approval_for_external_processes: bool = True


class ActivityStreamConfig(BaseModel):
    enabled: bool = True
    show_tool_args: ToolArgsDisplay = "preview"
    show_timers: bool = True
    show_result_summaries: bool = True
    compact: bool = False
    save_events: bool = False
    show_backend: bool = True
    show_subagents: bool = True


class AgentConfig(BaseModel):
    streaming_tools: bool = True
    stream_tool_events: bool = True
    stream_final_answer: bool = True

    # Execution budgets — prevent infinite loops while allowing multi-step tasks
    max_steps: int = 80
    max_tool_calls: int = 120
    max_consecutive_tool_calls: int = 30
    max_internal_tool_call_intercepts: int = 8
    max_repair_attempts: int = 3
    max_same_tool_retries: int = 3
    continue_until_task_complete: bool = True
    suppress_intermediate_planning: bool = True


class SkillsConfig(BaseModel):
    enabled: bool = True
    learned_enabled: bool = True
    auto_learn: bool = True
    auto_save_confidence_threshold: float = 0.8
    draft_confidence_threshold: float = 0.5
    max_injected_skills: int = 5


class MemoryConfig(BaseModel):
    enabled: bool = True
    database_path: str = ""
    max_context_messages: int = 24
    skills_enabled: bool = True
    max_recalled_skills: int = 4
    auto_recall: bool = True
    auto_summarize_sessions: bool = True
    store_tool_results: bool = True
    max_recall_items: int = 6
    project_context_enabled: bool = True
    compaction_enabled: bool = True
    compaction_keep_last_turns: int = 15
    compaction_trigger_ratio: float = 0.85

    @field_validator("max_context_messages")
    @classmethod
    def positive_context(cls, value: int) -> int:
        if value < 2:
            raise ValueError("max_context_messages must be at least 2")
        return value

    @field_validator("max_recalled_skills")
    @classmethod
    def non_negative_skills(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_recalled_skills cannot be negative")
        return value


class DockerExecutionConfig(BaseModel):
    enabled: bool = True
    image: str | None = None
    container: str | None = None
    mount_workspace: bool = True


class SSHExecutionConfig(BaseModel):
    enabled: bool = True
    host: str | None = None
    port: int = 22
    username: str | None = None
    key_path: str | None = None
    remote_workspace: str | None = None


class ExecutionConfig(BaseModel):
    backend: ExecutionBackend = "local"
    docker: DockerExecutionConfig = Field(default_factory=DockerExecutionConfig)
    ssh: SSHExecutionConfig = Field(default_factory=SSHExecutionConfig)


class SchedulerConfig(BaseModel):
    enabled: bool = True
    timezone: str = "local"
    max_concurrent_tasks: int = 2
    run_missed_tasks_on_startup: bool = False


class BrowserConfig(BaseModel):
    enabled: bool = True
    default: str = "auto"
    headless: bool = False
    cdp_endpoint: str | None = None
    custom_executable_path: str | None = None
    screenshot_dir: str = ".DonovanAgent/browser/screenshots"
    allow_external_sites: bool = True
    allowed_domains: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30


class SubagentsConfig(BaseModel):
    enabled: bool = True
    max_parallel: int = 3
    default_model: str = "same_as_main"
    allow_write_tools: bool = False
    show_activity: bool = True


class CheckpointsConfig(BaseModel):
    enabled: bool = True
    before_mutation: bool = True
    before_destructive_shell: bool = True
    max_checkpoints: int = 100
    auto_prune: bool = True


class PlanConfig(BaseModel):
    enabled: bool = True
    require_approval: bool = True
    dynamic_updates: bool = True
    show_checklist: bool = True
    default_for_complex_tasks: bool = False
    allow_safe_preflight: bool = False


class ThinkingConfig(BaseModel):
    enabled: bool = True
    show_safe_summaries: bool = True
    show_provider_reasoning_if_available: bool = False


class McpConfig(BaseModel):
    enabled: bool = True
    tool_search: bool = True
    defer_tools_above: int = 30
    always_load_servers: list[str] = Field(default_factory=list)
    always_load_tools: list[str] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file_logging: bool = True


class DonovanAgentConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    app: AppConfig = Field(default_factory=AppConfig)
    provider: ActiveProviderConfig = Field(default_factory=ActiveProviderConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # New config sections
    activity_stream: ActivityStreamConfig = Field(default_factory=ActivityStreamConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    subagents: SubagentsConfig = Field(default_factory=SubagentsConfig)
    checkpoints: CheckpointsConfig = Field(default_factory=CheckpointsConfig)
    plan: PlanConfig = Field(default_factory=PlanConfig)
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)

    @model_validator(mode="after")
    def sync_provider_alias(self) -> "DonovanAgentConfig":
        _map = {
            "openai": self.providers.openai,
            "openai_compatible": self.providers.custom,
            "anthropic": self.providers.anthropic,
            "deepseek": self.providers.deepseek,
            "lmstudio": self.providers.lmstudio,
            "qwen": self.providers.qwen,
        }
        src = _map.get(self.provider.active)
        if src is not None:
            self.provider.base_url = src.base_url
            self.provider.api_key_env = src.api_key_env
            self.provider.model = src.model
        elif self.provider.active == "ollama":
            self.provider.base_url = self.providers.ollama.base_url
            self.provider.api_key_env = self.providers.ollama.api_key_env
            self.provider.model = self.providers.ollama.model
        return self
