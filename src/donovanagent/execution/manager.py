from __future__ import annotations

from typing import Any

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.execution.base import ExecutionBackend
from donovanagent.execution.docker_backend import DockerExecutionBackend
from donovanagent.execution.local_backend import LocalExecutionBackend
from donovanagent.execution.ssh_backend import SSHExecutionBackend


class BackendManager:
    """Manages execution backends and provides the active backend."""

    def __init__(self, config: DonovanAgentConfig) -> None:
        self.config = config
        self._local: LocalExecutionBackend = LocalExecutionBackend()
        self._docker: DockerExecutionBackend | None = None
        self._ssh: SSHExecutionBackend | None = None
        self._active: ExecutionBackend | None = None

    @property
    def active(self) -> ExecutionBackend:
        if self._active is None:
            self._active = self._build_backend(self.config.execution.backend)
        return self._active

    @property
    def active_name(self) -> str:
        return self.active.name

    def _build_backend(self, backend_type: str) -> ExecutionBackend:
        if backend_type == "docker":
            if self._docker is None:
                exec_cfg = self.config.execution.docker
                self._docker = DockerExecutionBackend(
                    image=exec_cfg.image,
                    container=exec_cfg.container,
                    mount_workspace=exec_cfg.mount_workspace,
                    workspace=self.config.app.default_workspace,
                )
            return self._docker
        elif backend_type == "ssh":
            if self._ssh is None:
                ssh_cfg = self.config.execution.ssh
                self._ssh = SSHExecutionBackend(
                    host=ssh_cfg.host,
                    port=ssh_cfg.port,
                    username=ssh_cfg.username,
                    key_path=ssh_cfg.key_path,
                    remote_workspace=ssh_cfg.remote_workspace,
                )
            return self._ssh
        return self._local

    def switch(self, backend_type: str) -> str:
        """Switch to a different backend."""
        if backend_type not in ("local", "docker", "ssh"):
            raise ValueError(f"Unknown backend: {backend_type}. Use local, docker, or ssh.")
        self._active = self._build_backend(backend_type)
        self.config.execution.backend = backend_type  # type: ignore[assignment]
        return self.active.name

    def close(self) -> None:
        if self._docker:
            self._docker.close()
        if self._ssh:
            self._ssh.close()

    def __repr__(self) -> str:
        return f"BackendManager(active={self.active_name})"
