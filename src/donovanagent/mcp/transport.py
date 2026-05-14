"""MCP transport abstractions.

Supports stdio (subprocess), HTTP/Streamable HTTP, and SSE (legacy) transports.
"""

from __future__ import annotations

import abc
import json
import os
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from donovanagent.mcp.protocol import parse_json_rpc, McpError, json_rpc_request
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class McpTransport(abc.ABC):
    """Abstract base for an MCP transport connection."""

    @abc.abstractmethod
    def connect(self, timeout_ms: int = 60000) -> None:
        """Establish the transport connection."""
        ...

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Close the transport connection."""
        ...

    @abc.abstractmethod
    def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC request and return the response."""
        ...

    @abc.abstractmethod
    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        ...

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Check if the transport is connected."""
        ...

    @property
    @abc.abstractmethod
    def stderr_log(self) -> list[str]:
        """Return captured stderr/log output from the transport."""
        ...


class StdioMcpTransport(McpTransport):
    """MCP transport over stdio subprocess.

    Spawns a subprocess and communicates via JSON-RPC over stdin/stdout.
    Captures stderr as log output only.
    """

    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._stderr_log: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_log)

    def connect(self, timeout_ms: int = 60000) -> None:
        if self.is_connected:
            return

        resolved_cmd = self.command
        resolved_args = list(self.args)
        cmd_for_log = resolved_cmd

        # On Windows, try cmd /c passthrough for npm/npx/yarn/pnpm
        if platform.system() == "Windows" and resolved_cmd in ("npx", "npm", "yarn", "pnpm"):
            logger.info("Windows npx wrapper: wrapping %s with cmd /c", resolved_cmd)
            resolved_args = ["/c", resolved_cmd] + resolved_args
            resolved_cmd = os.environ.get("COMSPEC", "cmd.exe")
            cmd_for_log = f"cmd /c {resolved_cmd}"

        env = os.environ.copy()
        if self.env:
            sanitized = {k: v for k, v in self.env.items()}
            env.update(sanitized)

        logger.info("Starting MCP stdio server: %s %s", cmd_for_log, " ".join(resolved_args))

        try:
            self._process = subprocess.Popen(
                [resolved_cmd] + resolved_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,  # line-buffered
            )
        except FileNotFoundError:
            raise McpError(
                -32000,
                f"MCP server command not found: '{resolved_cmd}'. "
                f"Ensure it is installed and available in PATH.",
            ) from None
        except OSError as exc:
            raise McpError(-32000, f"Failed to start MCP server: {exc}") from None

        # Start stderr reader thread
        self._stderr_log = []
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        self._stderr_thread.start()

        # Wait briefly to catch immediate startup failures
        time.sleep(0.2)
        if self._process.poll() is not None:
            self._drain_stderr()
            stderr_text = "\n".join(self._stderr_log)
            raise McpError(
                -32000,
                f"MCP server exited immediately (code {self._process.returncode}).\n"
                f"Stderr: {stderr_text[:500] if stderr_text else '(empty)'}",
            )

    def _read_stderr(self) -> None:
        """Continuously read stderr from the subprocess."""
        try:
            assert self._process is not None and self._process.stderr is not None
            for line in self._process.stderr:
                line = line.rstrip("\n\r")
                if line:
                    self._stderr_log.append(line)
                    logger.debug("[MCP stderr] %s", line)
        except (ValueError, OSError):
            pass

    def _drain_stderr(self) -> None:
        """Drain any remaining stderr after process exit."""
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)
        if self._process and self._process.stderr:
            for line in self._process.stderr:
                line = line.rstrip("\n\r")
                if line:
                    self._stderr_log.append(line)

    def disconnect(self) -> None:
        if self._process is None:
            return

        logger.info("Stopping MCP stdio server: %s", self.command)

        # Try graceful shutdown via notification
        try:
            self.send_notification("exit")
        except Exception:
            pass

        # Terminate the process
        try:
            if self._process.poll() is None:
                self._process.terminate()
                self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
        except Exception:
            pass

        self._drain_stderr()
        self._process = None

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.is_connected:
            raise McpError(-32000, "MCP transport is not connected")

        assert self._process is not None and self._process.stdin is not None
        request_str = json_rpc_request(method, params)

        with self._lock:
            try:
                self._process.stdin.write(request_str + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._drain_stderr()
                stderr = "\n".join(self._stderr_log[-5:])
                raise McpError(
                    -32000,
                    f"MCP server process died while writing.\n"
                    f"Stderr (last 5 lines): {stderr[:300]}",
                ) from exc

            # Read response line
            try:
                assert self._process.stdout is not None
                response_line = self._process.stdout.readline()
            except (ValueError, OSError) as exc:
                raise McpError(-32000, f"Failed to read MCP response: {exc}") from exc

        if not response_line:
            self._drain_stderr()
            stderr = "\n".join(self._stderr_log[-5:])
            raise McpError(
                -32000,
                f"MCP server returned empty response (process may have exited).\n"
                f"Stderr (last 5 lines): {stderr[:300]}",
            )

        try:
            msg = parse_json_rpc(response_line.strip())
        except ValueError as exc:
            # Could be non-MCP output on stdout — log it
            logger.warning("Non-JSON-RPC output from MCP server: %s", response_line.strip()[:200])
            raise McpError(-32700, f"Invalid JSON-RPC from server: {exc}") from exc

        if "error" in msg:
            raise McpError.from_rpc(msg["error"])
        return msg.get("result", {})

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self.is_connected:
            raise McpError(-32000, "MCP transport is not connected")

        assert self._process is not None and self._process.stdin is not None
        notification_str = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or {}},
            ensure_ascii=False,
        )

        with self._lock:
            try:
                self._process.stdin.write(notification_str + "\n")
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpError(-32000, f"Failed to send notification: {exc}") from exc
