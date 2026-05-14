from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from donovanagent.config.paths import DonovanAgentPaths


SECRET_MARKERS = ("api_key", "token", "secret", "password", "authorization")


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        lowered = message.lower()
        if any(marker in lowered for marker in SECRET_MARKERS):
            record.msg = "[redacted log message containing sensitive marker]"
            record.args = ()
        return True


def configure_logging(paths: DonovanAgentPaths, level: str = "INFO", file_logging: bool = True) -> None:
    root = logging.getLogger("DonovanAgent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addFilter(SecretFilter())

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream_handler)

    if file_logging:
        paths.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            paths.log_dir / "DonovanAgent.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        file_handler.addFilter(SecretFilter())
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"DonovanAgent.{name}")
