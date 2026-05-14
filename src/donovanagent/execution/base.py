from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class ExecutionBackend(ABC):
    """Abstract execution backend for running commands across environments."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def run_command(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
        stream: bool = False,
    ) -> str:
        """Run a shell command and return output."""
        raise NotImplementedError

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read a file's contents."""
        raise NotImplementedError

    @abstractmethod
    def write_file(self, path: str, content: str) -> None:
        """Write content to a file."""
        raise NotImplementedError

    @abstractmethod
    def list_directory(self, path: str) -> list[dict[str, Any]]:
        """List directory contents."""
        raise NotImplementedError

    @abstractmethod
    def path_exists(self, path: str) -> bool:
        """Check if a path exists."""
        raise NotImplementedError

    @abstractmethod
    def get_system_info(self) -> dict[str, str]:
        """Get system information."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Clean up backend resources."""
        raise NotImplementedError
