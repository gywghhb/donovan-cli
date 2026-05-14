"""HTTP/Streamable HTTP and SSE (legacy) MCP transports."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from donovanagent.mcp.protocol import parse_json_rpc, McpError, json_rpc_request
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

# Required MCP Streamable HTTP headers for every request
_MCP_REQUIRED_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _merge_headers(user_headers: dict[str, str]) -> dict[str, str]:
    """Merge user-supplied headers with required MCP headers.

    Required MCP headers always take precedence so the protocol works.
    """
    merged = dict(user_headers)
    merged.update(_MCP_REQUIRED_HEADERS)
    return merged


def _parse_sse_for_response(sse_text: str, request_id: str) -> dict[str, Any]:
    """Parse an SSE stream and return the JSON-RPC result matching *request_id*.

    Handles:
    - comment lines starting with ``:``
    - ``event:`` / ``data:`` fields
    - multi-line data (joined with ``\\n``)
    - missing event (data-only events)
    - timeout if the stream ends without a match.
    """
    from donovanagent.mcp.protocol import McpError

    current_event = ""
    current_data: list[str] = []

    for line in sse_text.split("\n"):
        if line.startswith(":"):
            continue  # comment / keepalive
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data.append(line[6:])
        elif line == "":
            # End of an event block — try to match
            if current_data:
                data_str = "\n".join(current_data)
                try:
                    parsed = json.loads(data_str)
                    if isinstance(parsed, dict) and parsed.get("id") == request_id:
                        if "error" in parsed:
                            raise McpError.from_rpc(parsed["error"])
                        return parsed.get("result", {})
                except json.JSONDecodeError:
                    pass
            current_event = ""
            current_data = []

    # Flush remaining data if stream ended without blank line
    if current_data:
        data_str = "\n".join(current_data)
        try:
            parsed = json.loads(data_str)
            if isinstance(parsed, dict) and parsed.get("id") == request_id:
                if "error" in parsed:
                    raise McpError.from_rpc(parsed["error"])
                return parsed.get("result", {})
        except json.JSONDecodeError:
            pass

    raise McpError(
        -32000,
        "MCP SSE response did not contain a JSON-RPC response "
        f"matching request id '{request_id}'.",
    )


class HttpMcpTransport:
    """MCP transport over HTTP/Streamable HTTP.

    Uses HTTP POST for JSON-RPC requests.
    Streamable HTTP endpoints may return ``application/json`` or
    ``text/event-stream`` responses — both are handled transparently.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_ms: int = 60000,
    ) -> None:
        self.url = url.rstrip("/")
        self.user_headers = headers or {}
        self.timeout_ms = timeout_ms
        # Do NOT pass user headers to the client — they are merged per-request
        # so that required MCP headers always take precedence.
        self._client: httpx.Client = httpx.Client(
            timeout=httpx.Timeout(timeout_ms / 1000.0),
            follow_redirects=True,
        )
        self._connected = False
        self._stderr_log: list[str] = []
        self._session_id: str | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_log)

    def connect(self, timeout_ms: int = 60000) -> None:
        self._connected = True
        logger.info("MCP HTTP transport connected to %s", self.url)

    def disconnect(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._connected = False
        logger.info("MCP HTTP transport disconnected from %s", self.url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_headers(self) -> dict[str, str]:
        """Build headers for every HTTP POST: user headers + MCP required headers."""
        return _merge_headers(self.user_headers)

    def _handle_response(self, response: httpx.Response, request_id: str | None = None) -> dict[str, Any]:
        """Inspect the HTTP response and return the JSON-RPC result.

        Routes to JSON or SSE parsing based on Content-Type.
        """
        if response.status_code == 401:
            raise McpError(-32003, "MCP server returned 401 Unauthorized. Check your authentication.")
        if response.status_code == 403:
            raise McpError(-32003, "MCP server returned 403 Forbidden.")
        if response.status_code == 404:
            raise McpError(-32000, f"MCP server endpoint not found at {self.url}")
        if response.status_code >= 500:
            raise McpError(
                -32000,
                f"MCP server returned {response.status_code}. The server may be unavailable.",
            )
        if response.status_code == 406:
            raise McpError(
                -32000,
                "Not Acceptable: Client must accept both application/json and text/event-stream. "
                "Ensure the Accept header includes both MIME types.",
            )

        content_type = (response.headers.get("content-type") or "").lower()

        try:
            if "text/event-stream" in content_type:
                if request_id is None:
                    raise McpError(-32700, "MCP server returned SSE but no request id to match.")
                return _parse_sse_for_response(response.text, request_id)
            else:
                # Default to JSON parsing
                msg = parse_json_rpc(response.text)
                if "error" in msg:
                    raise McpError.from_rpc(msg["error"])
                return msg.get("result", {})
        except ValueError as exc:
            raise McpError(-32700, f"Invalid MCP response: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a JSON-RPC request over HTTP POST."""
        if not self._connected:
            raise McpError(-32000, "MCP HTTP transport is not connected")

        request_str = json_rpc_request(method, params)
        # Extract the request id so we can match SSE responses
        try:
            req_msg = json.loads(request_str)
            request_id = req_msg.get("id", "")
        except json.JSONDecodeError:
            request_id = ""

        try:
            response = self._client.post(
                self.url,
                content=request_str,
                headers=self._request_headers(),
            )
        except httpx.TimeoutException:
            raise McpError(-32004, f"MCP HTTP request timed out after {self.timeout_ms}ms")
        except httpx.ConnectError as exc:
            raise McpError(-32000, f"MCP HTTP connection failed: {exc}")
        except httpx.HTTPStatusError as exc:
            raise McpError(-32000, f"MCP HTTP error {exc.response.status_code}: {exc}")
        except httpx.RequestError as exc:
            raise McpError(-32000, f"MCP HTTP request failed: {exc}")

        return self._handle_response(response, request_id)

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification over HTTP POST."""
        if not self._connected:
            raise McpError(-32000, "MCP HTTP transport is not connected")

        notification_str = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or {}},
            ensure_ascii=False,
        )

        try:
            self._client.post(
                self.url,
                content=notification_str,
                headers=self._request_headers(),
            )
        except httpx.RequestError as exc:
            logger.warning("MCP HTTP notification failed: %s", exc)
        except Exception as exc:
            logger.warning("MCP HTTP notification error: %s", exc)


class SseMcpTransport:
    """Legacy MCP transport over Server-Sent Events.

    DEPRECATED: Prefer stdio or streamable HTTP transports.
    This implementation is provided for compatibility with existing MCP servers
    that only support SSE transport.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_ms: int = 60000,
    ) -> None:
        self.url = url.rstrip("/")
        self.user_headers = headers or {}
        self.timeout_ms = timeout_ms
        self._http_client: httpx.Client = httpx.Client(
            timeout=httpx.Timeout(timeout_ms / 1000.0),
            follow_redirects=True,
        )
        self._session_endpoint: str | None = None
        self._connected = False
        self._stderr_log: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_log)

    def connect(self, timeout_ms: int = 60000) -> None:
        """Connect to the SSE endpoint and discover the session endpoint."""
        try:
            response = self._http_client.get(
                self.url,
                headers=_merge_headers(self.user_headers),
            )
        except httpx.RequestError as exc:
            raise McpError(-32000, f"MCP SSE connection failed: {exc}")

        session_url = response.headers.get("x-session-url", "")
        if session_url:
            self._session_endpoint = session_url
        else:
            self._session_endpoint = self.url.rstrip("/sse") + "/message"

        self._connected = True
        logger.info(
            "MCP SSE transport connected to %s (session: %s)",
            self.url, self._session_endpoint,
        )

    def disconnect(self) -> None:
        try:
            self._http_client.close()
        except Exception:
            pass
        self._connected = False
        self._session_endpoint = None

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._connected or not self._session_endpoint:
            raise McpError(-32000, "MCP SSE transport is not connected")

        request_str = json_rpc_request(method, params)

        try:
            response = self._http_client.post(
                self._session_endpoint,
                content=request_str,
                headers=_merge_headers(self.user_headers),
            )
        except httpx.RequestError as exc:
            raise McpError(-32000, f"MCP SSE request failed: {exc}")

        if response.status_code == 401:
            raise McpError(-32003, "MCP SSE server returned 401 Unauthorized.")
        if response.status_code >= 500:
            raise McpError(-32000, f"MCP SSE server returned {response.status_code}.")

        try:
            msg = parse_json_rpc(response.text)
        except ValueError as exc:
            raise McpError(-32700, f"Invalid MCP SSE response: {exc}") from exc

        if "error" in msg:
            raise McpError.from_rpc(msg["error"])
        return msg.get("result", {})

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if not self._connected or not self._session_endpoint:
            return

        notification_str = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or {}},
            ensure_ascii=False,
        )

        try:
            self._http_client.post(
                self._session_endpoint,
                content=notification_str,
                headers=_merge_headers(self.user_headers),
            )
        except httpx.RequestError as exc:
            logger.warning("MCP SSE notification failed: %s", exc)
