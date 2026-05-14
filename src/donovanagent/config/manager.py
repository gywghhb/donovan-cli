from __future__ import annotations

import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from donovanagent.config.paths import DonovanAgentPaths, get_paths
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.utils.errors import ConfigError


ENV_OVERRIDES = {
    "DonovanAgent_PROVIDER": ("provider", "active"),
    "DonovanAgent_MODEL": ("provider", "model"),
    "DonovanAgent_BASE_URL": ("provider", "base_url"),
    "DonovanAgent_PERMISSION_MODE": ("app", "permission_mode"),
    "DonovanAgent_WORKSPACE": ("app", "default_workspace"),
    "DonovanAgent_SEARCH_ENABLED": ("search", "enabled"),
    "DonovanAgent_LOG_LEVEL": ("logging", "level"),
}


class ConfigManager:
    def __init__(self, paths: DonovanAgentPaths | None = None) -> None:
        raw_paths = paths or PathManager()
        self.paths = (
            raw_paths if isinstance(raw_paths, DonovanAgentPaths) else _coerce_paths(raw_paths)
        )

    def exists(self) -> bool:
        return self.paths.config_file.exists()

    def default_config(self) -> DonovanAgentConfig:
        config = DonovanAgentConfig()
        config.memory.database_path = str(self.paths.database_file)
        config.app.default_workspace = str(Path.cwd())
        config.security.approved_paths = [str(Path.cwd())]
        config.security.blocked_paths = default_blocked_paths()
        return config

    def load(self, create: bool = False) -> DonovanAgentConfig:
        self.paths.ensure()
        load_dotenv(self.paths.env_file, override=False)
        load_dotenv(Path.cwd() / ".env", override=False)

        if not self.paths.config_file.exists():
            config = self.default_config()
            if create:
                self.save(config)
            return config

        try:
            raw = yaml.safe_load(self.paths.config_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {self.paths.config_file}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Could not read config: {exc}") from exc

        try:
            config = DonovanAgentConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(str(exc)) from exc

        if not config.memory.database_path:
            config.memory.database_path = str(self.paths.database_file)
        if not config.security.blocked_paths:
            config.security.blocked_paths = default_blocked_paths()
        # Always use the directory where DonovanAgent was invoked as the workspace
        config.app.default_workspace = str(Path.cwd())
        self.apply_env_overrides(config)
        return config

    def save(self, config: DonovanAgentConfig) -> None:
        self.paths.ensure()
        sync_active_provider(config)
        data = config.model_dump(mode="json")
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        self.paths.config_file.write_text(text, encoding="utf-8")

    def apply_env_overrides(self, config: DonovanAgentConfig) -> None:
        for env_name, path in ENV_OVERRIDES.items():
            if env_name in {"DonovanAgent_MODEL", "DonovanAgent_BASE_URL"}:
                continue
            if env_name not in os.environ:
                continue
            self.set_value_on_config(config, ".".join(path), os.environ[env_name], validate=False)
        if os.getenv("DonovanAgent_MODEL"):
            set_active_provider_attr(config, "model", os.environ["DonovanAgent_MODEL"])
        if os.getenv("DonovanAgent_BASE_URL"):
            set_active_provider_attr(config, "base_url", os.environ["DonovanAgent_BASE_URL"])
        sync_active_provider(config)
        DonovanAgentConfig.model_validate(config.model_dump())

    def sanitized(self, config: DonovanAgentConfig) -> dict[str, Any]:
        data = deepcopy(config.model_dump(mode="json"))
        for provider in ("openai", "custom", "ollama"):
            env_name = data.get("providers", {}).get(provider, {}).get("api_key_env")
            if env_name:
                value = os.getenv(env_name, "")
                data["providers"][provider]["api_key_value"] = mask_secret(value) if value else ""
        env_name = data.get("search", {}).get("tavily_api_key_env")
        if env_name:
            value = os.getenv(env_name, "")
            data["search"]["tavily_api_key_value"] = mask_secret(value) if value else ""
        return data

    def set_value(self, key: str, value: str) -> DonovanAgentConfig:
        config = self.load(create=True)
        self.set_value_on_config(config, key, value, validate=True)
        if key in {"provider.model", "provider.base_url"}:
            set_active_provider_attr(config, key.split(".")[-1], str(parse_scalar(value)))
        sync_active_provider(config)
        self.save(config)
        return config

    def set_value_on_config(
        self, config: DonovanAgentConfig, key: str, value: str, validate: bool = True
    ) -> None:
        parts = key.split(".")
        if not parts:
            raise ConfigError("Config key cannot be empty")
        node: Any = config
        for part in parts[:-1]:
            if not hasattr(node, part):
                raise ConfigError(f"Unknown config key: {key}")
            node = getattr(node, part)
        leaf = parts[-1]
        if not hasattr(node, leaf):
            raise ConfigError(f"Unknown config key: {key}")
        setattr(node, leaf, parse_scalar(value))
        if validate:
            DonovanAgentConfig.model_validate(config.model_dump())

    def write_secret(self, env_name: str, value: str) -> None:
        self.paths.ensure()
        existing: dict[str, str] = {}
        if self.paths.env_file.exists():
            for line in self.paths.env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, raw_value = line.split("=", 1)
                    existing[key.strip()] = raw_value.strip()
        existing[env_name] = value
        body = "\n".join(f"{key}={quote_env(val)}" for key, val in sorted(existing.items())) + "\n"
        self.paths.env_file.write_text(body, encoding="utf-8")
        try:
            self.paths.env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.environ[env_name] = value


def PathManager() -> DonovanAgentPaths:
    """Backward-compatible path factory used by older tests and callers."""
    return get_paths()


def _path_attr(source: Any, name: str, fallback: Path) -> Path:
    value = getattr(source, name, fallback)
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    return fallback


def _coerce_paths(source: Any) -> DonovanAgentPaths:
    config_dir = _path_attr(source, "config_dir", Path(user_config_dir_placeholder()))
    data_dir = _path_attr(source, "data_dir", config_dir / "data")
    cache_dir = _path_attr(source, "cache_dir", data_dir / "cache")
    log_dir = _path_attr(source, "log_dir", data_dir / "logs")
    return DonovanAgentPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        log_dir=log_dir,
        config_file=_path_attr(source, "config_file", config_dir / "config.yaml"),
        env_file=_path_attr(source, "env_file", config_dir / ".env"),
        database_file=_path_attr(source, "database_file", data_dir / "DonovanAgent.db"),
        history_file=_path_attr(source, "history_file", data_dir / "prompt_history.txt"),
        temp_dir=_path_attr(source, "temp_dir", cache_dir / "tmp"),
    )


def user_config_dir_placeholder() -> str:
    return str(get_paths().config_dir)


def parse_scalar(value: str) -> Any:
    stripped = value.strip()
    lower = stripped.lower()
    if lower in {"true", "yes", "on"}:
        return True
    if lower in {"false", "no", "off"}:
        return False
    if lower in {"none", "null"}:
        return None
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            loaded = yaml.safe_load(stripped)
        except yaml.YAMLError:
            return stripped
        return loaded
    return stripped


def quote_env(value: str) -> str:
    if not value:
        return ""
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "#\"'"):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}...{value[-4:]}"


def default_blocked_paths() -> list[str]:
    home = Path.home()
    paths: list[str]
    if os.name == "nt":
        paths = [
            r"C:\Windows",
            r"C:\Program Files",
            r"C:\Program Files (x86)",
            r"C:\ProgramData",
            str(home / ".ssh"),
        ]
    else:
        paths = [
            "/",
            "/bin",
            "/sbin",
            "/usr",
            "/etc",
            "/var",
            "/System",
            "/Library",
            "/private",
            str(home / ".ssh"),
            str(home / ".gnupg"),
        ]
    return paths


def sync_active_provider(config: DonovanAgentConfig) -> None:
    if config.provider.active == "openai":
        config.provider.base_url = config.providers.openai.base_url
        config.provider.api_key_env = config.providers.openai.api_key_env
        config.provider.model = config.providers.openai.model
    elif config.provider.active == "openai_compatible":
        config.provider.base_url = config.providers.custom.base_url
        config.provider.api_key_env = config.providers.custom.api_key_env
        config.provider.model = config.providers.custom.model
    elif config.provider.active == "ollama":
        config.provider.base_url = config.providers.ollama.base_url
        config.provider.api_key_env = config.providers.ollama.api_key_env
        config.provider.model = config.providers.ollama.model


def set_active_provider_attr(config: DonovanAgentConfig, attr: str, value: str) -> None:
    if config.provider.active == "openai":
        setattr(config.providers.openai, attr, value)
    elif config.provider.active == "openai_compatible":
        setattr(config.providers.custom, attr, value)
    elif config.provider.active == "ollama" and attr in {"base_url", "model"}:
        setattr(config.providers.ollama, attr, value)
    else:
        setattr(config.provider, attr, value)
