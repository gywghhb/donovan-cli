from __future__ import annotations

from pathlib import Path

from donovanagent.app import DonovanAgentApp
from donovanagent.config.manager import ConfigManager
from donovanagent.config.schema import DonovanAgentConfig


def _make_minimal_config(manager: ConfigManager) -> DonovanAgentConfig:
    """Create a minimal config that passes validation without a real provider."""
    config = manager.load(create=True)
    # Set a dummy provider to bypass the "not configured" check
    config.provider.active = "openai"
    config.provider.model = "gpt-4.1"
    config.provider.api_key_env = "TEST_KEY"
    config.app.default_workspace = str(Path.cwd())
    config.app.first_run_complete = True
    manager.save(config)
    return config


def test_app_initialization(tmp_path: Path) -> None:
    """Test that DonovanAgentApp can be created with a minimal config."""
    from unittest.mock import patch

    # We need to use the real config manager but point it at a temp config dir
    with patch("donovanagent.config.manager.PathManager") as MockPaths:
        mock_paths = MockPaths.return_value
        mock_paths.config_dir = tmp_path / ".donovanagent"
        mock_paths.config_file = tmp_path / ".donovanagent" / "config.yaml"
        mock_paths.data_dir = tmp_path / ".donovanagent" / "data"
        mock_paths.history_file = tmp_path / ".donovanagent" / "history.txt"
        mock_paths.config_dir.mkdir(parents=True, exist_ok=True)

        manager = ConfigManager()
        # Set up config paths correctly
        from pathlib import Path as P
        import os
        # Override the paths on the manager
        manager.paths.config_dir.mkdir(parents=True, exist_ok=True)
        manager.paths.data_dir.mkdir(parents=True, exist_ok=True)

        # Write minimal valid config
        config = DonovanAgentConfig()
        config.provider.active = "openai"
        config.provider.model = "gpt-4.1"
        config.provider.api_key_env = "TEST_KEY"
        config.app.default_workspace = str(tmp_path)
        config.app.first_run_complete = True
        config.memory.database_path = str(tmp_path / "test.db")
        manager.save(config)

        app = DonovanAgentApp(manager=manager, assume_yes=True)
        assert app.config is not None
        assert app.db is not None
        assert app.config.provider.active == "openai"
