from __future__ import annotations

from pathlib import Path

from donovanagent.config.manager import ConfigManager, mask_secret
from donovanagent.config.paths import DonovanAgentPaths


def paths(tmp_path: Path) -> DonovanAgentPaths:
    return DonovanAgentPaths(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "log",
        config_file=tmp_path / "config" / "config.yaml",
        env_file=tmp_path / "config" / ".env",
        database_file=tmp_path / "data" / "DonovanAgent.db",
        history_file=tmp_path / "data" / "history.txt",
        temp_dir=tmp_path / "cache" / "tmp",
    )


def test_config_load_save(tmp_path: Path) -> None:
    manager = ConfigManager(paths(tmp_path))
    config = manager.default_config()
    config.provider.active = "ollama"
    config.providers.ollama.model = "qwen2.5-coder"
    manager.save(config)

    loaded = manager.load()
    assert loaded.provider.active == "ollama"
    assert loaded.provider.model == "qwen2.5-coder"
    assert loaded.memory.database_path.endswith("DonovanAgent.db")


def test_env_override_model(tmp_path: Path, monkeypatch) -> None:
    manager = ConfigManager(paths(tmp_path))
    config = manager.default_config()
    config.provider.active = "openai"
    config.providers.openai.model = "old"
    manager.save(config)

    monkeypatch.setenv("DonovanAgent_MODEL", "new-model")
    loaded = manager.load()
    assert loaded.provider.model == "new-model"
    assert loaded.providers.openai.model == "new-model"


def test_secret_masking() -> None:
    assert mask_secret("sk-1234567890abcd") == "sk-...abcd"
    assert mask_secret("short") == "*****"
