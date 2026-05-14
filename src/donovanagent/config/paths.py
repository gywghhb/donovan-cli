from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir, user_log_dir


APP_NAME = "DonovanAgent"
APP_AUTHOR = "Tudor Iustin"


@dataclass(frozen=True)
class DonovanAgentPaths:
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    log_dir: Path
    config_file: Path
    env_file: Path
    database_file: Path
    history_file: Path
    temp_dir: Path

    def ensure(self) -> None:
        for path in (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.log_dir,
            self.temp_dir,
            self.data_dir / "sessions",
        ):
            path.mkdir(parents=True, exist_ok=True)


def get_paths() -> DonovanAgentPaths:
    config_dir = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    data_dir = Path(user_data_dir(APP_NAME, APP_AUTHOR))
    cache_dir = Path(user_cache_dir(APP_NAME, APP_AUTHOR))
    log_dir = Path(user_log_dir(APP_NAME, APP_AUTHOR))
    return DonovanAgentPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        cache_dir=cache_dir,
        log_dir=log_dir,
        config_file=config_dir / "config.yaml",
        env_file=config_dir / ".env",
        database_file=data_dir / "DonovanAgent.db",
        history_file=data_dir / "prompt_history.txt",
        temp_dir=cache_dir / "tmp",
    )
