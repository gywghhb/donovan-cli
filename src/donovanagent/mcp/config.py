"""MCP server configuration models and multi-scope config store."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from donovanagent.config.paths import DonovanAgentPaths, get_paths

ServerType = Literal["stdio", "http", "streamable-http", "sse"]
TrustLevel = Literal["ask", "trusted", "blocked"]


_SECRET_ENV_PATTERN = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL|API_KEY|ACCESS_KEY)", re.I
)
_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

# URL query-parameter names whose values should be masked on display
_URL_SECRET_PARAM_PATTERN = re.compile(
    r"(secret|token|key|api_key|apikey|auth|authorization|password|credential|signature|sig)=[^&\s]+",
    re.I,
)


def mask_url(url: str) -> str:
    """Mask secret query parameters in URLs for safe display.

    Query-parameter values whose names contain recognised secret
    keywords are replaced with ``****``.
    """
    return _URL_SECRET_PARAM_PATTERN.sub(r"\1=****", url)


def mask_secret(value: str) -> str:
    """Mask sensitive values for safe display."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:3] + "..." + value[-4:]


def is_secret_var(name: str) -> bool:
    """Check if an environment variable name looks like it holds secrets."""
    return bool(_SECRET_ENV_PATTERN.search(name))


def expand_env_vars(value: str, extra_env: dict[str, str] | None = None) -> str:
    """Expand ${VAR_NAME} placeholders using environment variables and extra env dict."""

    def _replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        if extra_env and var in extra_env:
            return extra_env[var]
        val = os.environ.get(var, "")
        if not val and var == "HOME":
            val = str(Path.home())
        if not val and var == "USERPROFILE":
            val = str(Path.home())
        if not val and var == "DONOVAN_PROJECT_DIR":
            val = str(Path.cwd())
        return val

    return _ENV_VAR_PATTERN.sub(_replacer, value)


def expand_env_vars_in_dict(
    d: dict[str, Any], extra_env: dict[str, str] | None = None
) -> dict[str, Any]:
    """Recursively expand env vars in all string values in a dict."""
    result: dict[str, Any] = {}
    for key, value in d.items():
        if isinstance(value, str):
            result[key] = expand_env_vars(value, extra_env)
        elif isinstance(value, dict):
            result[key] = expand_env_vars_in_dict(value, extra_env)
        elif isinstance(value, list):
            result[key] = [
                expand_env_vars(item, extra_env) if isinstance(item, str) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def compute_config_hash(config: dict[str, Any]) -> str:
    """Compute a hash of the effective server config for trust invalidation.

    The hash covers command, args, env keys (not values), url, and headers keys.
    """
    relevant: dict[str, Any] = {}
    if "command" in config:
        relevant["command"] = config["command"]
        relevant["args"] = config.get("args", [])
        env_copy = {}
        for k in (config.get("env") or {}):
            env_copy[k] = ""  # only keys matter for trust
        relevant["env"] = env_copy
    if "url" in config:
        relevant["url"] = config["url"]
        headers_copy = {}
        for k in (config.get("headers") or {}):
            headers_copy[k] = ""
        relevant["headers"] = headers_copy
    raw = json.dumps(relevant, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class McpServerConfigModel(BaseModel):
    """Configuration for a single MCP server."""

    type: ServerType = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    trust: TrustLevel = "ask"
    timeout_ms: int = 60000
    max_output_tokens: int = 25000
    description: str = ""
    oauth: dict[str, str] | None = None

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: str) -> str:
        if v in ("streamable-http",):
            return "http"
        return v

    def effective_config_dict(self) -> dict[str, Any]:
        """Return the dict used for trust hash computation."""
        return self.model_dump(exclude={"enabled", "trust", "description", "timeout_ms", "max_output_tokens", "oauth"})

    def trust_hash(self) -> str:
        return compute_config_hash(self.effective_config_dict())

    def get_display_env(self) -> dict[str, str]:
        """Return env vars with secret values masked."""
        result = {}
        for key, value in self.env.items():
            if is_secret_var(key):
                result[key] = mask_secret(value)
            else:
                result[key] = value
        return result

    def get_display_headers(self) -> dict[str, str]:
        """Return headers with secret values masked."""
        result = {}
        for key, value in self.headers.items():
            if is_secret_var(key) or key.lower() in ("authorization", "x-api-key", "api-key"):
                result[key] = mask_secret(value)
            else:
                result[key] = value
        return result

    def resolve_env(self) -> dict[str, str]:
        """Resolve env vars from the config, expanding ${VAR} references."""
        merged = dict(os.environ)
        merged["DONOVAN_PROJECT_DIR"] = str(Path.cwd())
        resolved: dict[str, str] = {}
        for key, value in self.env.items():
            resolved[key] = expand_env_vars(value, merged)
        return resolved

    def resolved_command(self) -> str:
        """Return the command with env vars expanded."""
        return expand_env_vars(self.command)

    def resolved_args(self) -> list[str]:
        """Return args with env vars expanded."""
        return [expand_env_vars(a) for a in self.args]


ConfigScope = Literal["user", "project", "local"]


class McpSettings(BaseModel):
    """Complete MCP settings configuration."""

    mcpServers: dict[str, McpServerConfigModel] = Field(default_factory=dict)
    toolSearch: bool = True
    deferToolsAbove: int = 30
    alwaysLoadServers: list[str] = Field(default_factory=list)
    alwaysLoadTools: list[str] = Field(default_factory=list)


class McpConfigStore:
    """Multi-scope MCP configuration store.

    Loads and merges MCP server configs from three scopes:
    1. User scope:  ~/.donovan/mcp.json
    2. Project scope: <project>/.mcp.json
    3. Local scope:  <project>/.donovan/mcp.local.json

    Local overrides project, project overrides user for same-named servers.
    """

    def __init__(self, paths: DonovanAgentPaths | None = None, project_dir: str | None = None) -> None:
        self.paths = paths or get_paths()
        self.project_dir = Path(project_dir).resolve() if project_dir else Path.cwd().resolve()

    @property
    def user_config_path(self) -> Path:
        return self.paths.config_dir / "mcp.json"

    @property
    def project_config_path(self) -> Path:
        return self.project_dir / ".mcp.json"

    @property
    def local_config_path(self) -> Path:
        return self.project_dir / ".donovan" / "mcp.local.json"

    def _load_file(self, path: Path) -> dict[str, Any]:
        """Load a JSON config file, returning empty dict if not found."""
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
            return dict(json.loads(raw))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"Invalid MCP config at {path}: {exc}") from exc

    def _save_file(self, path: Path, data: dict[str, Any]) -> None:
        """Save config data to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")

    def load_server(self, name: str) -> tuple[McpServerConfigModel | None, ConfigScope | None]:
        """Load a single named server, checking scopes in priority order."""
        # Check local first (highest priority), then project, then user
        local_data = self._load_file(self.local_config_path)
        servers = local_data.get("mcpServers", {})
        if name in servers:
            return McpServerConfigModel(**servers[name]), "local"

        project_data = self._load_file(self.project_config_path)
        servers = project_data.get("mcpServers", {})
        if name in servers:
            return McpServerConfigModel(**servers[name]), "project"

        user_data = self._load_file(self.user_config_path)
        servers = user_data.get("mcpServers", {})
        if name in servers:
            return McpServerConfigModel(**servers[name]), "user"

        return None, None

    def load_all(self) -> dict[str, tuple[McpServerConfigModel, ConfigScope]]:
        """Load and merge all MCP server configs from all scopes.

        Returns dict mapping server name → (config, scope).
        Local overrides project, project overrides user.
        """
        merged: dict[str, tuple[McpServerConfigModel, ConfigScope]] = {}

        # User scope (lowest priority)
        user_data = self._load_file(self.user_config_path)
        for name, cfg in user_data.get("mcpServers", {}).items():
            merged[name] = (McpServerConfigModel(**cfg), "user")

        # Project scope (medium priority)
        project_data = self._load_file(self.project_config_path)
        for name, cfg in project_data.get("mcpServers", {}).items():
            merged[name] = (McpServerConfigModel(**cfg), "project")

        # Local scope (highest priority)
        local_data = self._load_file(self.local_config_path)
        for name, cfg in local_data.get("mcpServers", {}).items():
            merged[name] = (McpServerConfigModel(**cfg), "local")

        return merged

    def save_server(
        self, name: str, config: McpServerConfigModel, scope: ConfigScope = "project"
    ) -> None:
        """Save (or update) a server config in the given scope."""
        path = {
            "user": self.user_config_path,
            "project": self.project_config_path,
            "local": self.local_config_path,
        }[scope]

        data = self._load_file(path)
        servers = dict(data.get("mcpServers", {}))
        servers[name] = config.model_dump(mode="json")
        data["mcpServers"] = servers
        self._save_file(path, data)

    def remove_server(self, name: str, scope: ConfigScope | None = None) -> bool:
        """Remove a server config. If scope is None, removes from all scopes.

        Returns True if at least one instance was removed.
        """
        removed = False
        if scope is None or scope == "user":
            data = self._load_file(self.user_config_path)
            if name in data.get("mcpServers", {}):
                servers = dict(data.get("mcpServers", {}))
                del servers[name]
                data["mcpServers"] = servers
                self._save_file(self.user_config_path, data)
                removed = True

        if scope is None or scope == "project":
            data = self._load_file(self.project_config_path)
            if name in data.get("mcpServers", {}):
                servers = dict(data.get("mcpServers", {}))
                del servers[name]
                data["mcpServers"] = servers
                self._save_file(self.project_config_path, data)
                removed = True

        if scope is None or scope == "local":
            data = self._load_file(self.local_config_path)
            if name in data.get("mcpServers", {}):
                servers = dict(data.get("mcpServers", {}))
                del servers[name]
                data["mcpServers"] = servers
                self._save_file(self.local_config_path, data)
                removed = True

        return removed

    def list_servers(self) -> list[dict[str, Any]]:
        """List all configured servers with their scope and key metadata."""
        results: list[dict[str, Any]] = []
        for name, (config, scope) in self.load_all().items():
            results.append({
                "name": name,
                "type": config.type,
                "scope": scope,
                "enabled": config.enabled,
                "trust": config.trust,
                "command": config.command if config.type == "stdio" else "",
                "url": config.url if config.type in ("http", "sse") else "",
                "description": config.description,
            })
        return sorted(results, key=lambda x: x["name"])
