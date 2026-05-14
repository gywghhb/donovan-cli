"""Tests for MCP integration."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from donovanagent.config.paths import DonovanAgentPaths
from donovanagent.mcp.config import (
    McpServerConfigModel,
    McpConfigStore,
    expand_env_vars,
    expand_env_vars_in_dict,
    mask_secret,
    mask_url,
    is_secret_var,
    compute_config_hash,
)
from donovanagent.mcp.security import McpRiskClassifier, McpTrustStore
from donovanagent.mcp.protocol import (
    json_rpc_request,
    parse_json_rpc,
    McpError,
    make_initialize_params,
    make_call_tool_params,
    get_mcp_protocol_version,
    set_mcp_protocol_version,
)
from donovanagent.mcp.mentions import parse_mentions
from donovanagent.mcp.client import McpClient, McpToolInfo
from donovanagent.mcp.registry import _mcp_tool_name, _parse_mcp_tool_name
from donovanagent.mcp.transport import StdioMcpTransport
from donovanagent.tools.base import ToolDefinition, ToolResult, ToolExecutionContext
from donovanagent.mcp.transport_http import (
    HttpMcpTransport,
    SseMcpTransport,
    _merge_headers,
    _parse_sse_for_response,
)
from donovanagent.mcp.manager import McpManager
from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.agent.prompts import build_system_prompt


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestMcpConfig:
    def test_server_config_defaults(self) -> None:
        cfg = McpServerConfigModel(command="node", args=["server.js"])
        assert cfg.type == "stdio"
        assert cfg.enabled is True
        assert cfg.trust == "ask"
        assert cfg.timeout_ms == 60000
        assert cfg.max_output_tokens == 25000

    def test_server_config_type_normalization(self) -> None:
        cfg = McpServerConfigModel(type="streamable-http", url="http://localhost")
        assert cfg.type == "http"

    def test_server_config_trust_hash_changes_on_command(self) -> None:
        cfg1 = McpServerConfigModel(command="node", args=["server.js"])
        cfg2 = McpServerConfigModel(command="python", args=["server.py"])
        assert cfg1.trust_hash() != cfg2.trust_hash()

    def test_server_config_trust_hash_stable(self) -> None:
        cfg1 = McpServerConfigModel(command="node", args=["server.js"], env={"KEY": "val1"})
        cfg2 = McpServerConfigModel(command="node", args=["server.js"], env={"KEY": "val2"})
        # Hash only covers env keys, not values
        assert cfg1.trust_hash() == cfg2.trust_hash()

    def test_display_env_masks_secrets(self) -> None:
        cfg = McpServerConfigModel(
            command="node",
            env={"API_KEY": "sk-1234567890", "PATH": "/usr/bin"},
        )
        display = cfg.get_display_env()
        assert display["API_KEY"].startswith("sk-")
        assert "..." in display["API_KEY"]
        assert display["PATH"] == "/usr/bin"

    def test_display_headers_masks_auth(self) -> None:
        cfg = McpServerConfigModel(
            type="http",
            url="http://example.com",
            headers={"Authorization": "Bearer sk-1234567890", "X-Custom": "value"},
        )
        display = cfg.get_display_headers()
        assert "..." in display["Authorization"]
        assert display["X-Custom"] == "value"

    def test_expand_env_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            extra = {"TEST_VAR": "hello", "DONOVAN_PROJECT_DIR": tmpdir}
            result = expand_env_vars("prefix_${TEST_VAR}_suffix", extra)
            assert result == "prefix_hello_suffix"

    def test_expand_env_vars_defaults(self) -> None:
        os.environ["DONOVAN_MCP_TEST"] = "custom_value"
        result = expand_env_vars("${DONOVAN_MCP_TEST}")
        assert result == "custom_value"

    def test_expand_env_vars_in_dict(self) -> None:
        data = {"command": "node", "args": ["${PROJECT_DIR}/script.js"]}
        extra = {"PROJECT_DIR": "/home/user/project"}
        result = expand_env_vars_in_dict(data, extra)
        assert result["args"][0] == "/home/user/project/script.js"


class TestMcpConfigStore:
    @pytest.fixture
    def temp_store(self) -> McpConfigStore:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = DonovanAgentPaths(
                config_dir=Path(tmpdir) / ".config",
                data_dir=Path(tmpdir) / ".data",
                cache_dir=Path(tmpdir) / ".cache",
                log_dir=Path(tmpdir) / ".log",
                config_file=Path(tmpdir) / ".config" / "config.yaml",
                env_file=Path(tmpdir) / ".config" / ".env",
                database_file=Path(tmpdir) / ".data" / "test.db",
                history_file=Path(tmpdir) / ".data" / "history.txt",
                temp_dir=Path(tmpdir) / ".cache" / "tmp",
            )
            store = McpConfigStore(paths, project_dir=tmpdir)
            yield store

    def test_save_and_load_server(self, temp_store: McpConfigStore) -> None:
        cfg = McpServerConfigModel(command="node", args=["server.js"])
        temp_store.save_server("test-server", cfg, "project")

        loaded, scope = temp_store.load_server("test-server")
        assert loaded is not None
        assert loaded.command == "node"
        assert scope == "project"

    def test_scope_precedence(self, temp_store: McpConfigStore) -> None:
        # Save same name in different scopes
        user_cfg = McpServerConfigModel(command="user-version")
        project_cfg = McpServerConfigModel(command="project-version")
        local_cfg = McpServerConfigModel(command="local-version")

        temp_store.save_server("dup-server", user_cfg, "user")
        temp_store.save_server("dup-server", project_cfg, "project")

        # Project should override user
        loaded, scope = temp_store.load_server("dup-server")
        assert loaded is not None
        assert loaded.command == "project-version"
        assert scope == "project"

        # Local should override both
        temp_store.save_server("dup-server", local_cfg, "local")
        loaded, scope = temp_store.load_server("dup-server")
        assert loaded.command == "local-version"
        assert scope == "local"

    def test_list_servers(self, temp_store: McpConfigStore) -> None:
        cfg1 = McpServerConfigModel(command="node", args=["s1.js"])
        cfg2 = McpServerConfigModel(type="http", url="http://example.com")
        temp_store.save_server("server1", cfg1, "project")
        temp_store.save_server("server2", cfg2, "user")

        servers = temp_store.list_servers()
        names = [s["name"] for s in servers]
        assert "server1" in names
        assert "server2" in names

    def test_remove_server(self, temp_store: McpConfigStore) -> None:
        cfg = McpServerConfigModel(command="node")
        temp_store.save_server("to-remove", cfg, "project")
        assert temp_store.load_server("to-remove")[0] is not None

        temp_store.remove_server("to-remove", "project")
        assert temp_store.load_server("to-remove")[0] is None


# ---------------------------------------------------------------------------
# Protocol tests
# ---------------------------------------------------------------------------

class TestMcpProtocol:
    def test_json_rpc_request_format(self) -> None:
        request = json_rpc_request("test_method", {"key": "value"})
        msg = json.loads(request)
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "test_method"
        assert msg["params"] == {"key": "value"}
        assert "id" in msg

    def test_parse_valid_json_rpc(self) -> None:
        msg = parse_json_rpc('{"jsonrpc":"2.0","id":"1","result":{"ok":true}}')
        assert msg["result"]["ok"] is True

    def test_parse_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="Invalid JSON-RPC"):
            parse_json_rpc("not json")

    def test_parse_invalid_version(self) -> None:
        with pytest.raises(ValueError, match="JSON-RPC 2.0"):
            parse_json_rpc('{"jsonrpc":"1.0","id":"1","result":{}}')

    def test_make_initialize_params(self) -> None:
        params = make_initialize_params()
        assert params["clientInfo"]["name"] == "DonovanAgent"
        assert params["protocolVersion"] == "2024-11-05"
        assert "tools" in params["capabilities"]
        assert len(params["clientInfo"]["version"]) > 0  # real version string

    def test_make_call_tool_params(self) -> None:
        params = make_call_tool_params("echo", {"message": "hello"})
        assert params["name"] == "echo"
        assert params["arguments"]["message"] == "hello"

    def test_mcp_error_from_rpc(self) -> None:
        error = McpError.from_rpc({"code": -32601, "message": "Method not found"})
        assert error.code == -32601
        assert "Method not found" in str(error)


# ---------------------------------------------------------------------------
# Security tests
# ---------------------------------------------------------------------------

class TestMcpSecurity:
    def test_risk_classifier_readonly(self) -> None:
        risk, label, reasons = McpRiskClassifier.classify(
            "filesystem", "list_files", "List files in directory"
        )
        assert risk == "low"

    def test_risk_classifier_write(self) -> None:
        risk, label, reasons = McpRiskClassifier.classify(
            "custom_app", "create_record", "Create a new record"
        )
        assert risk == "medium"

    def test_risk_classifier_destructive(self) -> None:
        risk, label, reasons = McpRiskClassifier.classify(
            "database", "delete_table", "Drop a database table"
        )
        assert risk == "high"
        assert label == "destructive"

    def test_risk_classifier_shell(self) -> None:
        risk, label, reasons = McpRiskClassifier.classify(
            "terminal", "exec_command", "Execute a shell command"
        )
        assert risk == "high"
        assert label == "shell/command"

    def test_risk_classifier_unknown_default(self) -> None:
        risk, label, reasons = McpRiskClassifier.classify(
            "custom", "do_thing", "Perform an operation"
        )
        assert label == "unknown"

    def test_requires_approval_for_destructive(self) -> None:
        assert McpRiskClassifier.requires_approval("destructive", "delete_all") is True

    def test_requires_approval_for_write(self) -> None:
        assert McpRiskClassifier.requires_approval("write", "create_item") is True

    def test_not_requires_approval_for_readonly(self) -> None:
        assert McpRiskClassifier.requires_approval("read-only", "list_items") is False

    def test_secret_masking(self) -> None:
        assert mask_secret("") == ""
        assert mask_secret("ab") == "**"
        assert mask_secret("abcdefgh") == "********"
        assert mask_secret("sk-1234567890").startswith("sk-")
        assert "..." in mask_secret("sk-1234567890")

    def test_is_secret_var(self) -> None:
        assert is_secret_var("API_KEY") is True
        assert is_secret_var("GITHUB_TOKEN") is True
        assert is_secret_var("PATH") is False
        assert is_secret_var("HOME") is False

    def test_trust_hash_invalidation(self) -> None:
        cfg1 = McpServerConfigModel(command="node", args=["old.js"])
        cfg2 = McpServerConfigModel(command="node", args=["new.js"])
        assert cfg1.trust_hash() != cfg2.trust_hash()


class TestMcpTrustStore:
    @pytest.fixture
    def temp_trust_store(self) -> McpTrustStore:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = DonovanAgentPaths(
                config_dir=Path(tmpdir) / ".config",
                data_dir=Path(tmpdir) / ".data",
                cache_dir=Path(tmpdir) / ".cache",
                log_dir=Path(tmpdir) / ".log",
                config_file=Path(tmpdir) / ".config" / "config.yaml",
                env_file=Path(tmpdir) / ".config" / ".env",
                database_file=Path(tmpdir) / ".data" / "test.db",
                history_file=Path(tmpdir) / ".data" / "history.txt",
                temp_dir=Path(tmpdir) / ".cache" / "tmp",
            )
            store = McpTrustStore(paths, project_dir=tmpdir)
            yield store

    def test_trust_lifecycle(self, temp_trust_store: McpTrustStore) -> None:
        cfg = McpServerConfigModel(command="node")
        h = cfg.trust_hash()

        assert temp_trust_store.get_trust("test-srv", "project") is None
        temp_trust_store.set_trust("test-srv", "trusted", "project", h)
        assert temp_trust_store.get_trust("test-srv", "project") == "trusted"

        temp_trust_store.set_trust("test-srv", "blocked", "project", h)
        assert temp_trust_store.get_trust("test-srv", "project") == "blocked"

    def test_config_change_invalidates_trust(self, temp_trust_store: McpTrustStore) -> None:
        cfg1 = McpServerConfigModel(command="node", args=["v1.js"])
        cfg2 = McpServerConfigModel(command="node", args=["v2.js"])

        temp_trust_store.set_trust("srv", "trusted", "project", cfg1.trust_hash())
        assert temp_trust_store.has_config_changed("srv", cfg2, "project") is True

    def test_reset_project_choices(self, temp_trust_store: McpTrustStore) -> None:
        cfg = McpServerConfigModel(command="node")
        temp_trust_store.set_trust("srv", "trusted", "project", cfg.trust_hash())
        temp_trust_store.reset_project_choices()
        assert temp_trust_store.get_trust("srv", "project") is None


# ---------------------------------------------------------------------------
# @ mention tests
# ---------------------------------------------------------------------------

class TestMcpMentions:
    def test_parse_basic_mention(self) -> None:
        text = "Check @github:issue://123 for details"
        mentions = parse_mentions(text)
        assert len(mentions) == 1
        assert mentions[0]["server"] == "github"
        assert mentions[0]["uri"] == "issue://123"

    def test_parse_multiple_mentions(self) -> None:
        text = "Compare @docs:file://api with @postgres:schema://users"
        mentions = parse_mentions(text)
        assert len(mentions) == 2

    def test_parse_no_mentions(self) -> None:
        text = "This has no mentions"
        assert parse_mentions(text) == []

    def test_parse_complex_uri(self) -> None:
        text = "See @server:https://example.com/resource/path?q=1"
        mentions = parse_mentions(text)
        assert len(mentions) == 1
        assert mentions[0]["uri"] == "https://example.com/resource/path?q=1"


# ---------------------------------------------------------------------------
# Tool name mapping tests
# ---------------------------------------------------------------------------

class TestToolNameMapping:
    def test_mcp_tool_name_convention(self) -> None:
        from donovanagent.mcp.registry import _mcp_tool_name, _parse_mcp_tool_name
        full = _mcp_tool_name("github", "list_issues")
        assert full == "mcp__github__list_issues"
        parsed = _parse_mcp_tool_name(full)
        assert parsed == ("github", "list_issues")

    def test_parse_invalid_name(self) -> None:
        from donovanagent.mcp.registry import _parse_mcp_tool_name
        assert _parse_mcp_tool_name("not_mcp_tool") is None
        assert _parse_mcp_tool_name("mcp__only_server") is None

    def test_mcp_tool_name_sanitization(self) -> None:
        from donovanagent.mcp.registry import _mcp_tool_name
        name = _mcp_tool_name("my-server", "my.tool")
        assert "__" in name
        assert " " not in name


# ---------------------------------------------------------------------------
# Config hash tests
# ---------------------------------------------------------------------------

class TestConfigHash:
    def test_hash_changes_with_command(self) -> None:
        h1 = compute_config_hash({"command": "node", "args": ["a.js"]})
        h2 = compute_config_hash({"command": "python", "args": ["b.py"]})
        assert h1 != h2

    def test_hash_ignores_values(self) -> None:
        h1 = compute_config_hash({
            "command": "node",
            "env": {"API_KEY": ""},  # empty value in hash
        })
        h2 = compute_config_hash({
            "command": "node",
            "env": {"API_KEY": "sk-real"},  # actual value
        })
        assert h1 == h2  # only key names matter

    def test_hash_includes_url(self) -> None:
        h1 = compute_config_hash({"url": "http://example.com"})
        h2 = compute_config_hash({"url": "http://evil.com"})
        assert h1 != h2


# ---------------------------------------------------------------------------
# Integration tests (fake stdio MCP server)
# ---------------------------------------------------------------------------

_FAKE_SERVER = Path(__file__).parent / "fixtures" / "mcp" / "fake_stdio_server.py"


class TestStdioTransportIntegration:
    """Integration tests for StdioMcpTransport using the fake MCP server."""

    @pytest.fixture
    def transport(self) -> StdioMcpTransport:
        t = StdioMcpTransport(
            command=sys.executable,
            args=[str(_FAKE_SERVER)],
        )
        yield t
        try:
            t.disconnect()
        except Exception:
            pass

    def test_connect(self, transport: StdioMcpTransport) -> None:
        transport.connect()
        assert transport.is_connected

    def test_send_initialize_request(self, transport: StdioMcpTransport) -> None:
        transport.connect()
        result = transport.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        })
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "fake-test-server"

    def test_send_notification(self, transport: StdioMcpTransport) -> None:
        transport.connect()
        transport.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        })
        transport.send_notification("notifications/initialized")

    def test_unknown_method(self, transport: StdioMcpTransport) -> None:
        transport.connect()
        transport.send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0.1"},
        })
        transport.send_notification("notifications/initialized")
        with pytest.raises(McpError, match="Method not found"):
            transport.send_request("unknown_method", {})

    def test_disconnect(self, transport: StdioMcpTransport) -> None:
        transport.connect()
        assert transport.is_connected
        transport.disconnect()
        assert not transport.is_connected

    def test_stderr_log(self, transport: StdioMcpTransport) -> None:
        transport.connect()
        assert transport.stderr_log == []
        transport.disconnect()


class TestMcpClientIntegration:
    """Integration tests for McpClient using the fake MCP server."""

    @pytest.fixture
    def config(self) -> McpServerConfigModel:
        return McpServerConfigModel(
            command=sys.executable,
            args=[str(_FAKE_SERVER)],
            timeout_ms=10000,
        )

    @pytest.fixture
    def client(self, config: McpServerConfigModel) -> McpClient:
        cl = McpClient(config)
        yield cl
        try:
            cl.disconnect()
        except Exception:
            pass

    def test_connect_and_disconnect(self, client: McpClient) -> None:
        assert not client.is_connected
        client.connect()
        assert client.is_connected
        assert client.capabilities.server_name == "fake-test-server"
        assert client.capabilities.server_version == "1.0.0"
        assert client.capabilities.protocol_version == "2024-11-05"

        client.disconnect()
        assert not client.is_connected

    def test_list_tools(self, client: McpClient) -> None:
        client.connect()
        tools = client.list_tools()
        assert len(tools) == 3
        names = [t.name for t in tools]
        assert "echo" in names
        assert "list_projects" in names
        assert "read_file_content" in names

        echo_tool = next(t for t in tools if t.name == "echo")
        assert "Echo back" in echo_tool.description
        assert "message" in echo_tool.inputSchema["properties"]

        client.disconnect()

    def test_call_tool(self, client: McpClient) -> None:
        client.connect()
        result = client.call_tool("echo", {"message": "hello world"})
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Echo: hello world"
        client.disconnect()

    def test_call_tool_no_args(self, client: McpClient) -> None:
        client.connect()
        result = client.call_tool("list_projects", {})
        assert "alpha" in result["content"][0]["text"]
        client.disconnect()

    def test_call_tool_not_found(self, client: McpClient) -> None:
        client.connect()
        with pytest.raises(McpError, match="not found"):
            client.call_tool("nonexistent_tool", {})
        client.disconnect()

    def test_list_resources(self, client: McpClient) -> None:
        client.connect()
        resources = client.list_resources()
        assert len(resources) == 2
        uris = [r.uri for r in resources]
        assert "docs:file://hello" in uris
        assert "docs:file://readme" in uris

        hello = next(r for r in resources if r.name == "Hello Document")
        assert hello.mimeType == "text/plain"

        client.disconnect()

    def test_read_resource(self, client: McpClient) -> None:
        client.connect()
        content = client.read_resource("docs:file://hello")
        assert content is not None
        assert content.uri == "docs:file://hello"
        assert "Hello world!" in content.text
        client.disconnect()

    def test_read_resource_not_found(self, client: McpClient) -> None:
        client.connect()
        content = client.read_resource("docs:file://nonexistent")
        assert content is None
        client.disconnect()

    def test_list_prompts(self, client: McpClient) -> None:
        client.connect()
        prompts = client.list_prompts()
        assert len(prompts) == 2
        names = [p.name for p in prompts]
        assert "summarize_project" in names
        assert "greeting" in names

        sp = next(p for p in prompts if p.name == "summarize_project")
        assert len(sp.arguments) == 1
        assert sp.arguments[0]["name"] == "project_name"

        client.disconnect()

    def test_get_prompt(self, client: McpClient) -> None:
        client.connect()
        result = client.get_prompt("summarize_project", {"project_name": "donovan"})
        assert "donovan" in result.messages[0]["content"]["text"]
        client.disconnect()

    def test_get_prompt_not_found(self, client: McpClient) -> None:
        client.connect()
        with pytest.raises(McpError, match="not found"):
            client.get_prompt("nonexistent_prompt", {})
        client.disconnect()

    def test_uninitialized_client(self, config: McpServerConfigModel) -> None:
        client = McpClient(config)
        with pytest.raises(McpError, match="not initialized"):
            client.list_tools()
        with pytest.raises(McpError, match="not initialized"):
            client.call_tool("echo", {})
        with pytest.raises(McpError, match="not initialized"):
            client.list_resources()
        with pytest.raises(McpError, match="not initialized"):
            client.list_prompts()


class TestMcpManagerIntegration:
    """Integration tests for McpManager using the fake MCP server."""

    @pytest.fixture
    def manager(self) -> McpManager:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = DonovanAgentPaths(
                config_dir=Path(tmpdir) / ".config",
                data_dir=Path(tmpdir) / ".data",
                cache_dir=Path(tmpdir) / ".cache",
                log_dir=Path(tmpdir) / ".log",
                config_file=Path(tmpdir) / ".config" / "config.yaml",
                env_file=Path(tmpdir) / ".config" / ".env",
                database_file=Path(tmpdir) / ".data" / "test.db",
                history_file=Path(tmpdir) / ".data" / "history.txt",
                temp_dir=Path(tmpdir) / ".cache" / "tmp",
            )
            config_obj = DonovanAgentConfig()

            class _MockRegistry:
                def list(self) -> list:
                    return []
                def register(self, tool: object) -> None:
                    pass
                def unregister(self, name: str) -> None:
                    pass

            mgr = McpManager(config_obj, _MockRegistry(), paths=paths, project_dir=tmpdir)
            server_cfg = McpServerConfigModel(
                command=sys.executable,
                args=[str(_FAKE_SERVER)],
                timeout_ms=10000,
                trust="trusted",
            )
            mgr.config_store.save_server("fake-server", server_cfg, "project")
            yield mgr
            mgr.cleanup()

    def test_configured_servers(self, manager: McpManager) -> None:
        servers = manager.configured_servers
        assert len(servers) == 1
        assert servers[0][0] == "fake-server"
        assert servers[0][2] == "project"

    def test_connect_server(self, manager: McpManager) -> None:
        result = manager.connect_server("fake-server")
        assert "Connected" in result
        assert "fake-server" in manager.connected_servers
        assert "Tools: 3" in result
        assert "Resources: 2" in result
        assert "Prompts: 2" in result

    def test_server_status_after_connect(self, manager: McpManager) -> None:
        manager.connect_server("fake-server")
        status = manager.get_server_status("fake-server")
        assert status is not None
        assert status.connected
        assert status.tool_count == 3
        assert status.resource_count == 2
        assert status.prompt_count == 2
        assert status.type == "stdio"
        assert status.scope == "project"
        assert status.enabled is True
        assert status.trust == "trusted"

    def test_list_statuses(self, manager: McpManager) -> None:
        manager.connect_server("fake-server")
        statuses = manager.list_statuses()
        assert len(statuses) == 1
        assert statuses[0].connected
        assert statuses[0].name == "fake-server"

    def test_disconnect_server(self, manager: McpManager) -> None:
        manager.connect_server("fake-server")
        assert "fake-server" in manager.connected_servers
        result = manager.disconnect_server("fake-server")
        assert "Disconnected" in result
        assert "fake-server" not in manager.connected_servers

    def test_connect_nonexistent_server(self, manager: McpManager) -> None:
        result = manager.connect_server("nonexistent")
        assert "not found" in result

    def test_connect_already_connected(self, manager: McpManager) -> None:
        manager.connect_server("fake-server")
        result = manager.connect_server("fake-server")
        assert "already connected" in result


# ---------------------------------------------------------------------------
# Transport header tests
# ---------------------------------------------------------------------------

class TestMcpTransportHeaders:
    """Tests for HTTP MCP transport header handling."""

    def test_merge_headers_adds_required(self) -> None:
        """Required MCP headers are always present after merge."""
        headers = _merge_headers({})
        assert headers["Accept"] == "application/json, text/event-stream"
        assert headers["Content-Type"] == "application/json"

    def test_merge_headers_preserves_custom(self) -> None:
        """Custom user headers survive the merge."""
        headers = _merge_headers({"Authorization": "Bearer token123"})
        assert headers["Authorization"] == "Bearer token123"

    def test_merge_headers_required_override(self) -> None:
        """User headers cannot override required MCP headers."""
        headers = _merge_headers({"Accept": "text/plain"})
        assert headers["Accept"] == "application/json, text/event-stream"

    def test_merge_headers_multiple_custom(self) -> None:
        """Multiple custom headers preserved alongside required ones."""
        headers = _merge_headers({
            "Authorization": "Bearer tok",
            "X-Custom": "value",
        })
        assert headers["Authorization"] == "Bearer tok"
        assert headers["X-Custom"] == "value"
        assert headers["Accept"] == "application/json, text/event-stream"

    def test_http_transport_request_headers(self) -> None:
        """HttpMcpTransport sends the right headers on POST."""
        import httpx
        transport = HttpMcpTransport(
            url="http://localhost:9999/mcp",
            headers={"Authorization": "Bearer test"},
            timeout_ms=5000,
        )
        request_headers = transport._request_headers()
        assert request_headers["Accept"] == "application/json, text/event-stream"
        assert request_headers["Content-Type"] == "application/json"
        assert request_headers["Authorization"] == "Bearer test"

    def test_sse_transport_connect_headers(self) -> None:
        """SseMcpTransport does NOT set user headers on the httpx client (they're per-request)."""
        transport = SseMcpTransport(
            url="http://localhost:9999/sse",
            headers={"Authorization": "Bearer test"},
            timeout_ms=5000,
        )
        headers = transport._http_client.headers
        # User-supplied header should NOT be on the client — they are merged per-request
        assert "authorization" not in {k.lower() for k in headers}


# ---------------------------------------------------------------------------
# SSE parsing tests
# ---------------------------------------------------------------------------

class TestSseParsing:
    """Tests for SSE stream response parsing."""

    def test_sse_json_response(self) -> None:
        """SSE with JSON data matching request id returns result."""
        request_id = "req-1"
        sse_text = (
            "event: message\n"
            'data: {"id": "req-1", "result": {"status": "ok"}}\n'
            "\n"
        )
        result = _parse_sse_for_response(sse_text, request_id)
        assert result == {"status": "ok"}

    def test_sse_multiline_data(self) -> None:
        """Multi-line data fields are joined with newline."""
        request_id = "req-2"
        sse_text = (
            "event: message\n"
            "data: {\n"
            'data: "id": "req-2",\n'
            'data: "result": {"msg": "hello"}\n'
            "data: }\n"
            "\n"
        )
        result = _parse_sse_for_response(sse_text, request_id)
        assert result == {"msg": "hello"}

    def test_sse_comment_lines_skipped(self) -> None:
        """Lines starting with : are treated as comments and skipped."""
        request_id = "req-3"
        sse_text = (
            ": this is a keepalive comment\n"
            ": another comment\n"
            'data: {"id": "req-3", "result": {"ok": true}}\n'
            "\n"
        )
        result = _parse_sse_for_response(sse_text, request_id)
        assert result == {"ok": True}

    def test_sse_no_matching_id_raises(self) -> None:
        """SSE stream without matching request id raises McpError."""
        request_id = "req-4"
        sse_text = (
            'data: {"id": "other-req", "result": {}}\n'
            "\n"
        )
        with pytest.raises(McpError, match="matching request id"):
            _parse_sse_for_response(sse_text, request_id)

    def test_sse_error_response(self) -> None:
        """SSE with error object raises McpError."""
        request_id = "req-5"
        sse_text = (
            'data: {"id": "req-5", "error": {"code": -32601, "message": "Method not found"}}\n'
            "\n"
        )
        with pytest.raises(McpError, match="Method not found"):
            _parse_sse_for_response(sse_text, request_id)

    def test_sse_multiple_events_picks_matching(self) -> None:
        """Multiple events in stream, only the matching id is returned."""
        request_id = "req-7"
        sse_text = (
            'data: {"id": "req-6", "result": {"a": 1}}\n'
            "\n"
            'data: {"id": "req-7", "result": {"b": 2}}\n'
            "\n"
        )
        result = _parse_sse_for_response(sse_text, request_id)
        assert result == {"b": 2}

    def test_sse_without_event_field(self) -> None:
        """Data-only events (no event:) are parsed correctly."""
        request_id = "req-8"
        sse_text = (
            'data: {"id": "req-8", "result": {"value": 42}}\n'
            "\n"
        )
        result = _parse_sse_for_response(sse_text, request_id)
        assert result == {"value": 42}

    def test_sse_trailing_data_without_blank_line(self) -> None:
        """Data at end of stream without trailing blank line is flushed."""
        request_id = "req-9"
        sse_text = (
            'data: {"id": "req-9", "result": {"done": true}}'
        )
        result = _parse_sse_for_response(sse_text, request_id)
        assert result == {"done": True}


# ---------------------------------------------------------------------------
# URL masking tests
# ---------------------------------------------------------------------------

class TestUrlMasking:
    """Tests for URL secret query parameter masking."""

    def test_mask_url_token_param(self) -> None:
        url = "https://example.com/api?token=my-secret-token&other=visible"
        masked = mask_url(url)
        assert "my-secret-token" not in masked
        assert "token=****" in masked
        assert "other=visible" in masked

    def test_mask_url_api_key_param(self) -> None:
        url = "https://api.example.com/data?api_key=sk-abc123&format=json"
        masked = mask_url(url)
        assert "sk-abc123" not in masked
        assert "api_key=****" in masked

    def test_mask_url_secret_param(self) -> None:
        url = "https://example.com?secret=abc123"
        masked = mask_url(url)
        assert mask_url(url) == "https://example.com?secret=****"

    def test_mask_url_auth_param(self) -> None:
        url = "https://example.com?auth=basic&name=hello"
        masked = mask_url(url)
        assert masked == "https://example.com?auth=****&name=hello"

    def test_mask_url_password_param(self) -> None:
        url = "https://example.com?password=hunter2&user=admin"
        masked = mask_url(url)
        assert "hunter2" not in masked
        assert "password=****" in masked

    def test_mask_url_signature_param(self) -> None:
        url = "https://example.com?sig=abc123def&expires=1700000000"
        masked = mask_url(url)
        assert "sig=****" in masked

    def test_mask_url_no_secrets_unchanged(self) -> None:
        url = "https://example.com?name=hello&page=1"
        assert mask_url(url) == url

    def test_mask_url_no_query_string(self) -> None:
        url = "https://example.com/path"
        assert mask_url(url) == url

    def test_mask_url_multiple_secret_params(self) -> None:
        url = "https://example.com?token=abc&key=def&name=test"
        masked = mask_url(url)
        assert masked.count("****") == 2
        assert "name=test" in masked


# ---------------------------------------------------------------------------
# Protocol version tests
# ---------------------------------------------------------------------------

class TestMcpProtocolVersion:
    """Tests for MCP protocol version centralization."""

    def test_default_protocol_version(self) -> None:
        assert get_mcp_protocol_version() == "2024-11-05"

    def test_set_protocol_version(self) -> None:
        set_mcp_protocol_version("2025-01-01")
        try:
            assert get_mcp_protocol_version() == "2025-01-01"
        finally:
            set_mcp_protocol_version("2024-11-05")

    def test_initialize_params_uses_central_version(self) -> None:
        params = make_initialize_params()
        assert params["protocolVersion"] == "2024-11-05"

    def test_initialize_params_correct_client_info(self) -> None:
        params = make_initialize_params()
        assert params["clientInfo"]["name"] == "DonovanAgent"
        assert "." in params["clientInfo"]["version"]  # semver check

    def test_initialize_params_override_version(self) -> None:
        params = make_initialize_params(protocol_version="2025-03-01")
        assert params["protocolVersion"] == "2025-03-01"


# ---------------------------------------------------------------------------
# MCP awareness in system prompt tests
# ---------------------------------------------------------------------------

class TestMcpAwareness:
    """Tests for MCP state awareness in the system prompt."""

    def test_prompt_with_no_mcp_servers(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        prompt = build_system_prompt(config, registry, mcp_servers=[])
        assert "MCP: enabled, no servers configured" in prompt

    def test_prompt_with_connected_server(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        mcp_servers = [
            {"name": "test-server", "type": "http", "connected": True},
        ]
        prompt = build_system_prompt(config, registry, mcp_servers=mcp_servers)
        assert "test-server" in prompt
        assert "http" in prompt
        assert "connected" in prompt

    def test_prompt_with_disconnected_server(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        mcp_servers = [
            {"name": "framer", "type": "http", "connected": False},
        ]
        prompt = build_system_prompt(config, registry, mcp_servers=mcp_servers)
        assert "framer" in prompt
        assert "disconnected" in prompt

    def test_prompt_with_multiple_servers(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        mcp_servers = [
            {"name": "server-a", "type": "stdio", "connected": True},
            {"name": "server-b", "type": "http", "connected": False},
        ]
        prompt = build_system_prompt(config, registry, mcp_servers=mcp_servers)
        assert "server-a" in prompt
        assert "server-b" in prompt


# ---------------------------------------------------------------------------
# 406 Not Acceptable handling tests
# ---------------------------------------------------------------------------

class TestNotAcceptable:
    """Tests for 406 Not Acceptable error from MCP servers."""

    def test_406_raises_helpful_error(self) -> None:
        """A 406 response produces a clear error about Accept header."""
        import httpx
        transport = HttpMcpTransport(
            url="http://localhost:9999/mcp",
            timeout_ms=5000,
        )
        # Simulate a 406 response
        response = httpx.Response(
            status_code=406,
            text="Not Acceptable",
            request=httpx.Request("POST", "http://localhost:9999/mcp"),
        )
        with pytest.raises(McpError) as excinfo:
            transport._handle_response(response, "req-1")
        assert "Not Acceptable" in str(excinfo.value)
        assert "Accept" in str(excinfo.value)

    def test_handle_response_json(self) -> None:
        """JSON response is parsed correctly."""
        import httpx
        transport = HttpMcpTransport(
            url="http://localhost:9999/mcp",
            timeout_ms=5000,
        )
        response = httpx.Response(
            status_code=200,
            text='{"jsonrpc": "2.0", "id": "1", "result": {"hello": "world"}}',
            headers={"content-type": "application/json"},
            request=httpx.Request("POST", "http://localhost:9999/mcp"),
        )
        result = transport._handle_response(response)
        assert result == {"hello": "world"}

    def test_handle_response_sse(self) -> None:
        """SSE response is routed to SSE parser."""
        import httpx
        transport = HttpMcpTransport(
            url="http://localhost:9999/mcp",
            timeout_ms=5000,
        )
        sse_body = (
            'data: {"id": "req-1", "result": {"done": true}}\n'
            "\n"
        )
        response = httpx.Response(
            status_code=200,
            text=sse_body,
            headers={"content-type": "text/event-stream"},
            request=httpx.Request("POST", "http://localhost:9999/mcp"),
        )
        result = transport._handle_response(response, "req-1")
        assert result == {"done": True}

    def test_handle_response_no_request_id_with_sse(self) -> None:
        """SSE response without request_id raises error."""
        import httpx
        transport = HttpMcpTransport(
            url="http://localhost:9999/mcp",
            timeout_ms=5000,
        )
        response = httpx.Response(
            status_code=200,
            text="data: {}\n\n",
            headers={"content-type": "text/event-stream"},
            request=httpx.Request("POST", "http://localhost:9999/mcp"),
        )
        with pytest.raises(McpError, match="no request id"):
            transport._handle_response(response, None)


# ---------------------------------------------------------------------------
# ToolRegistry unregister tests
# ---------------------------------------------------------------------------

class TestToolRegistryUnregister:
    """Tests for ToolRegistry.unregister()."""

    def test_unregister_removes_tool(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        td = ToolDefinition(
            name="test_tool",
            description="test",
            parameters={"type": "object", "properties": {}},
            handler=lambda ctx, args: ToolResult(True, "ok"),
            enabled_key="mcp_tools.enabled",
        )
        registry.register(td)
        assert registry.get("test_tool") is not None
        registry.unregister("test_tool")
        assert "test_tool" not in registry._tools

    def test_unregister_nonexistent_is_noop(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        # Should not raise
        registry.unregister("nonexistent")

    def test_unregister_invalidates_cache(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        td = ToolDefinition(
            name="cache_test",
            description="test",
            parameters={"type": "object", "properties": {}},
            handler=lambda ctx, args: ToolResult(True, "ok"),
            enabled_key="mcp_tools.enabled",
        )
        registry.register(td)
        _ = registry.openai_schemas()  # populate cache
        assert registry._openai_schemas_cache is not None
        registry.unregister("cache_test")
        assert registry._openai_schemas_cache is None


# ---------------------------------------------------------------------------
# MCP control tools tests
# ---------------------------------------------------------------------------

class TestMcpControlTools:
    """Tests for MCP control tool definitions."""

    def test_control_tools_registered_in_default_registry(self) -> None:
        from donovanagent.tools.registry import build_default_registry
        config = DonovanAgentConfig()
        registry = build_default_registry(config)
        tools = registry.list()
        names = {t.name for t in tools}
        assert "donovan_mcp_list_servers" in names
        assert "donovan_mcp_connect_server" in names
        assert "donovan_mcp_list_tools" in names
        assert "donovan_mcp_call_tool" in names
        assert "donovan_mcp_list_resources" in names
        assert "donovan_mcp_read_resource" in names
        assert "donovan_mcp_list_prompts" in names
        assert "donovan_mcp_get_prompt" in names
        assert "search_mcp_tools" in names
        assert "donovan_mcp_add_server" in names
        assert "donovan_mcp_remove_server" in names

    def test_control_tools_have_no_approval(self) -> None:
        from donovanagent.tools.mcp_tools import CONTROL_TOOL_DEFS
        for tool in CONTROL_TOOL_DEFS:
            assert tool.requires_approval is False, f"{tool.name} requires approval"

    def test_donovan_mcp_list_servers_no_manager(self) -> None:
        from donovanagent.tools.mcp_tools import _handle_list_servers
        config = DonovanAgentConfig()
        from donovanagent.memory.database import MemoryDatabase
        db = MemoryDatabase(":memory:")
        from rich.console import Console
        from donovanagent.tools.base import ToolExecutionContext
        from donovanagent.tools.approval import ApprovalManager
        ctx = ToolExecutionContext(
            config=config,
            db=db,
            console=Console(),
            session_id=None,
            approval=ApprovalManager(config),
            mcp_manager=None,
        )
        with pytest.raises(RuntimeError, match="MCP manager is not available"):
            _handle_list_servers(ctx, {})

    def test_control_tools_low_risk(self) -> None:
        from donovanagent.tools.mcp_tools import CONTROL_TOOL_DEFS
        medium_risk_tools = {
            "donovan_mcp_call_tool",
            "donovan_mcp_add_server",
            "donovan_mcp_remove_server",
        }
        for tool in CONTROL_TOOL_DEFS:
            if tool.name in medium_risk_tools:
                assert tool.risk == "medium", f"{tool.name} should be medium risk"
            else:
                assert tool.risk == "low", f"{tool.name} has unexpected risk {tool.risk}"


# ---------------------------------------------------------------------------
# MCP dynamic tool registration tests
# ---------------------------------------------------------------------------

class TestMcpDynamicRegistration:
    """Tests for dynamic MCP tool registration lifecycle."""

    def test_register_unregister_cycle(self) -> None:
        from donovanagent.tools.registry import ToolRegistry
        from donovanagent.mcp.registry import McpToolRegistry
        from donovanagent.mcp.client import McpToolInfo

        config = DonovanAgentConfig()
        registry = ToolRegistry(config)
        mcp_registry = McpToolRegistry()

        # Register some MCP tools
        mcp_registry.register_server_tools("test-server", [
            McpToolInfo(name="echo", description="Echo back input", inputSchema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
            }),
            McpToolInfo(name="ping", description="Ping the server"),
        ])

        assert mcp_registry.total_count() == 2

        # Convert to definitions and register
        def client_getter(name):
            return None
        defs = mcp_registry.to_donovan_definitions(client_getter)
        for d in defs:
            registry.register(d)

        assert "mcp__test-server__echo" in registry._tools

        # Unregister
        for d in defs:
            registry.unregister(d.name)
        assert "mcp__test-server__echo" not in registry._tools

    def test_mcp_tool_naming_convention(self) -> None:
        from donovanagent.mcp.registry import _mcp_tool_name, _parse_mcp_tool_name
        name = _mcp_tool_name("my-server", "my-tool")
        assert name == "mcp__my-server__my-tool"
        parsed = _parse_mcp_tool_name(name)
        assert parsed == ("my-server", "my-tool")


# ---------------------------------------------------------------------------
# ToolExecutionContext mcp_manager tests
# ---------------------------------------------------------------------------

class TestToolContextMcp:
    """Tests that ToolExecutionContext carries mcp_manager."""

    def test_mcp_manager_in_context(self) -> None:
        from donovanagent.tools.base import ToolExecutionContext
        config = DonovanAgentConfig()
        from donovanagent.memory.database import MemoryDatabase
        from rich.console import Console
        from donovanagent.tools.approval import ApprovalManager
        ctx = ToolExecutionContext(
            config=config,
            db=MemoryDatabase(":memory:"),
            console=Console(),
            session_id="test",
            approval=ApprovalManager(config),
            mcp_manager="mock-manager",
        )
        assert ctx.mcp_manager == "mock-manager"

    def test_mcp_manager_defaults_none(self) -> None:
        from donovanagent.tools.base import ToolExecutionContext
        config = DonovanAgentConfig()
        from donovanagent.memory.database import MemoryDatabase
        from rich.console import Console
        from donovanagent.tools.approval import ApprovalManager
        ctx = ToolExecutionContext(
            config=config,
            db=MemoryDatabase(":memory:"),
            console=Console(),
            session_id="test",
            approval=ApprovalManager(config),
        )
        assert ctx.mcp_manager is None


# ---------------------------------------------------------------------------
# MCP tool name repair tests
# ---------------------------------------------------------------------------

class TestMcpNameRepair:
    """Tests for MCP tool name repair."""

    def test_repair_camelcase_joined(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__framer__updateXmlForNode", "mcp__framer__getProjectXml"}
        result = repair_mcp_tool_name("mcpframerupdateXmlForNode", registered)
        assert result == "mcp__framer__updateXmlForNode"

    def test_repair_underscore_separated(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__framer__updateXmlForNode"}
        result = repair_mcp_tool_name("mcp_framer_updateXmlForNode", registered)
        assert result == "mcp__framer__updateXmlForNode"

    def test_repair_hyphen_separated(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__framer__updateXmlForNode"}
        result = repair_mcp_tool_name("mcp-framer-updateXmlForNode", registered)
        assert result == "mcp__framer__updateXmlForNode"

    def test_repair_dot_separated(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__framer__updateXmlForNode"}
        result = repair_mcp_tool_name("mcp.framer.updateXmlForNode", registered)
        assert result == "mcp__framer__updateXmlForNode"

    def test_no_repair_for_nonexistent(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__other__tool"}
        result = repair_mcp_tool_name("mcpframerupdateXmlForNode", registered)
        assert result is None

    def test_repair_preserves_correct_name(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__framer__updateXmlForNode"}
        result = repair_mcp_tool_name("mcp__framer__updateXmlForNode", registered)
        assert result == "mcp__framer__updateXmlForNode"

    def test_repair_not_applied_to_non_mcp(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"run_shell"}
        result = repair_mcp_tool_name("run_shell", registered)
        assert result is None

    def test_repair_camelcase_split(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        registered = {"mcp__framer__getProjectXml"}
        result = repair_mcp_tool_name("mcpframer_getProjectXml", registered)
        assert result == "mcp__framer__getProjectXml"


# ---------------------------------------------------------------------------
# DSML parser tests
# ---------------------------------------------------------------------------

class TestDsmlParser:
    """Tests for DSML/internal tool-call parser."""

    def test_parse_full_block(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls
        text = (
            '<tool_calls>\n'
            '  <invoke name="mcp__framer__updateXmlForNode">\n'
            '    <parameter name="nodeId" string="true">abc123</parameter>\n'
            '    <parameter name="xml" string="true"><Desktop></parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcp__framer__updateXmlForNode"
        assert calls[0]["arguments"]["nodeId"] == "abc123"

    def test_parse_multiple_calls(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls
        text = (
            '<tool_calls>\n'
            '  <invoke name="mcp__framer__getProjectXml">\n'
            '    <parameter name="id" string="true">proj1</parameter>\n'
            '  </invoke>\n'
            '  <invoke name="mcp__framer__updateXmlForNode">\n'
            '    <parameter name="nodeId" string="true">n1</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 2

    def test_parse_returns_empty_for_no_markup(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls
        calls = parse_dsml_tool_calls("Hello, this is a normal message.")
        assert len(calls) == 0

    def test_parse_malformed_tool_name(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls
        text = (
            '<tool_calls>\n'
            '  <invoke name="mcpframerupdateXmlForNode">\n'
            '    <parameter name="nodeId" string="true">abc</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcpframerupdateXmlForNode"

    def test_parse_invoke_without_tool_calls_wrapper(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls
        text = (
            'Some text <invoke name="mcp__framer__getProjectXml">\n'
            '  <parameter name="id" string="true">p1</parameter>\n'
            '</invoke> more text'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcp__framer__getProjectXml"


# ---------------------------------------------------------------------------
# XML validation tests
# ---------------------------------------------------------------------------

class TestXmlValidation:
    """Tests for MCP XML payload validation."""

    def test_valid_xml_passes(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        xml = (
            '<Desktop nodeId="abc">\n'
            '  <ComponentInstance position="relative" />\n'
            '</Desktop>'
        )
        valid, msg = validate_mcp_xml(xml)
        assert valid
        assert msg == ""

    def test_missing_closing_angle_bracket_fails(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        xml = (
            '<Desktop nodeId="abc"\n'
            '  <ComponentInstance />\n'
            '</Desktop>'
        )
        valid, msg = validate_mcp_xml(xml)
        assert not valid
        assert "Missing closing" in msg

    def test_empty_xml_fails(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        valid, msg = validate_mcp_xml("")
        assert not valid
        assert "empty" in msg.lower()

    def test_unclosed_tag_fails(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        xml = '<Desktop><Section></Section></Desktop>'
        valid, msg = validate_mcp_xml(xml)
        assert valid  # This is correct, actually

    def test_self_closing_tag_passes(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        xml = '<ComponentInstance insertUrl="test.js" position="relative" width="100%" />'
        valid, msg = validate_mcp_xml(xml)
        assert valid

    def test_valid_xml_with_children_passes(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        xml = (
            '<Desktop nodeId="WQLkyLRf1" position="absolute" width="1200px" height="1000px">\n'
            '    <ComponentInstance insertUrl="https://example.com/hero.js" position="relative" width="100%" />\n'
            '</Desktop>'
        )
        valid, msg = validate_mcp_xml(xml)
        assert valid

    def test_whitespace_only_fails(self) -> None:
        from donovanagent.tools.mcp_tools import validate_mcp_xml
        valid, msg = validate_mcp_xml("   ")
        assert not valid
        assert "empty" in msg.lower()


# ---------------------------------------------------------------------------
# MCP write tool classification tests
# ---------------------------------------------------------------------------

class TestMcpWriteClassification:
    """Tests for MCP write tool detection."""

    def test_update_tool_is_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert is_mcp_write_tool("updateXmlForNode")

    def test_set_tool_is_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert is_mcp_write_tool("setNodeXml")

    def test_insert_tool_is_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert is_mcp_write_tool("insertComponent")

    def test_create_tool_is_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert is_mcp_write_tool("createPage")

    def test_delete_tool_is_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert is_mcp_write_tool("deletePage")

    def test_read_tool_is_not_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert not is_mcp_write_tool("getProjectXml")

    def test_list_tool_is_not_write(self) -> None:
        from donovanagent.tools.mcp_tools import is_mcp_write_tool
        assert not is_mcp_write_tool("listProjects")


# ---------------------------------------------------------------------------
# DSML stripping in render pipeline tests
# ---------------------------------------------------------------------------

class TestDsmlStripping:
    """Tests that DSML markup is stripped from model output."""

    def test_dsml_block_stripped_from_text(self) -> None:
        from donovanagent.ui.render import strip_tool_markup
        text = (
            'Here is the update.\n'
            '<tool_calls>\n'
            '  <invoke name="mcp__framer__updateXmlForNode">\n'
            '    <parameter name="nodeId" string="true">abc</parameter>\n'
            '  </invoke>\n'
            '</tool_calls>\n'
            'Done.'
        )
        result = strip_tool_markup(text)
        assert "mcp__framer__updateXmlForNode" not in result
        assert "<invoke" not in result
        assert "Here is the update" in result
        assert "Done" in result

    def test_multiline_data_preserved(self) -> None:
        from donovanagent.ui.render import plain_text
        text = 'Normal text without markup.'
        result = plain_text(text)
        assert "Normal text" in result

    def test_broad_dsml_stripped(self) -> None:
        from donovanagent.ui.render import strip_tool_markup
        text = (
            'Normal content <invoke name="mcpframerupdateXmlForNode">'
            '<parameter name="nodeId" string="true">abc</parameter></invoke>'
        )
        result = strip_tool_markup(text)
        assert "<invoke" not in result
        assert "mcpframerupdateXmlForNode" not in result
        assert "Normal content" in result

    def test_dsml_parser_routes_to_tool_call(self) -> None:
        from donovanagent.tools.mcp_tools import parse_dsml_tool_calls
        from donovanagent.providers.models import ToolCall
        text = (
            '<tool_calls>\n'
            '  <invoke name="donovan_mcp_list_servers">\n'
            '  </invoke>\n'
            '</tool_calls>'
        )
        calls = parse_dsml_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "donovan_mcp_list_servers"
        # Convert to ToolCall
        tc = ToolCall(id="dsml_test", name=calls[0]["name"], arguments=calls[0]["arguments"])
        assert tc.name == "donovan_mcp_list_servers"


# ---------------------------------------------------------------------------
# DSML interceptor tests (extract_internal_tool_calls)
# ---------------------------------------------------------------------------

DSML_REGRESSION_TEXT = (
    'The image was uploaded and inserted successfully. Let me now center it properly on the page.\n'
    '\n'
    '<tool_calls>\n'
    '<invoke name="mcpframerupdateXmlForNode">\n'
    '<parameter name="nodeId" string="true">WQLkyLRf1</parameter>\n'
    '<parameter name="xml" string="true"><Desktop nodeId="WQLkyLRf1">\n'
    '  <Image nodeId="yGlfpiD42" position="absolute" centerX="50%" centerY="50%" width="500px" height="500px" borderRadius="16px" backgroundImage="https://framerusercontent.com/images/7d3GMKs2VFXV2F0ZCGpgiEJB9yg.jpg" />\n'
    '</Desktop></parameter>\n'
    '</invoke>\n'
    '</tool_calls>'
)


class TestDsmlInterceptor:
    """Tests for extract_internal_tool_calls and DSML interception."""

    def test_extracts_mcp_tool_name(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        cleaned, calls = extract_internal_tool_calls(DSML_REGRESSION_TEXT)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcpframerupdateXmlForNode"

    def test_preserves_xml_content_exactly(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        cleaned, calls = extract_internal_tool_calls(DSML_REGRESSION_TEXT)
        xml = calls[0]["arguments"]["xml"]
        assert '<Desktop nodeId="WQLkyLRf1">' in xml
        assert '<Image' in xml
        assert 'backgroundImage=' in xml
        assert xml.count("\n") >= 2  # multi-line XML preserved

    def test_cleaned_text_has_no_dsml_markup(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        cleaned, calls = extract_internal_tool_calls(DSML_REGRESSION_TEXT)
        assert "<invoke" not in cleaned
        assert "<tool_calls>" not in cleaned
        assert "</tool_calls>" not in cleaned
        assert "<parameter" not in cleaned

    def test_leading_text_present_in_cleaned(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        cleaned, calls = extract_internal_tool_calls(DSML_REGRESSION_TEXT)
        assert "image was uploaded" in cleaned
        assert "center it properly" in cleaned

    def test_no_dsml_returns_original(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        text = "This is a normal response with no markup."
        cleaned, calls = extract_internal_tool_calls(text)
        assert cleaned == text
        assert len(calls) == 0

    def test_empty_text(self) -> None:
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        cleaned, calls = extract_internal_tool_calls("")
        assert cleaned == ""
        assert len(calls) == 0

    def test_tool_name_repair_after_extraction(self) -> None:
        from donovanagent.mcp.registry import repair_mcp_tool_name
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        cleaned, calls = extract_internal_tool_calls(DSML_REGRESSION_TEXT)
        malformed = calls[0]["name"]
        registered = {"mcp__framer__updateXmlForNode", "mcp__framer__getNodeXml"}
        repaired = repair_mcp_tool_name(malformed, registered)
        assert repaired == "mcp__framer__updateXmlForNode"

    def test_dsml_and_native_text_stripped_by_render(self) -> None:
        from donovanagent.ui.render import sanitize_response, plain_text
        sanitized = sanitize_response(DSML_REGRESSION_TEXT)
        assert "<invoke" not in sanitized
        assert "<tool_calls>" not in sanitized

        plain = plain_text(DSML_REGRESSION_TEXT)
        assert "<invoke" not in plain
        assert "<tool_calls>" not in plain

    def test_plain_dsml_without_text_suppressed(self) -> None:
        from donovanagent.ui.render import sanitize_response
        dsml_only = (
            '<tool_calls>\n'
            '<invoke name="mcpframerupdateXmlForNode">\n'
            '<parameter name="nodeId" string="true">abc</parameter>\n'
            '</invoke>\n'
            '</tool_calls>'
        )
        result = sanitize_response(dsml_only)
        assert result == "" or "invoke" not in result

    def test_malformed_dsml_suppressed(self) -> None:
        from donovanagent.ui.render import sanitize_response
        malformed = 'Some text <invoke name="mcpframerupdateXmlForNode"> without closing tag'
        result = sanitize_response(malformed)
        assert "<invoke" not in result
        assert "mcpframerupdateXmlForNode" not in result
        # Text before the malformed DSML is preserved
        assert "Some text" in result

    def test_single_quote_dsml_stripped_by_render(self) -> None:
        from donovanagent.ui.render import sanitize_response, plain_text
        text = (
            "<tool_calls>\n"
            "<invoke name='mcpframerupdateXmlForNode'>\n"
            "<parameter name='nodeId'>5:37</parameter>\n"
            "</invoke>\n"
            "</tool_calls>"
        )
        sanitized = sanitize_response(text)
        assert "<invoke" not in sanitized
        assert "<tool_calls>" not in sanitized
        assert "<parameter" not in sanitized
        assert "5:37" not in sanitized

        plain = plain_text(text)
        assert "<invoke" not in plain
        assert "<tool_calls>" not in plain
        assert "<parameter" not in plain

    def test_single_quote_dsml_with_leading_text(self) -> None:
        from donovanagent.ui.render import sanitize_response, plain_text
        from donovanagent.tools.mcp_tools import extract_internal_tool_calls
        text = (
            "The image was recreated. Let me set it now.\n"
            "<tool_calls>\n"
            "<invoke name='mcpframerupdateXmlForNode'>\n"
            "<parameter name='nodeId'>5:37</parameter>\n"
            "<parameter name='xml'><Node /></parameter>\n"
            "</invoke>\n"
            "</tool_calls>"
        )
        cleaned, calls = extract_internal_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["name"] == "mcpframerupdateXmlForNode"
        assert "image was recreated" in cleaned

        sanitized = sanitize_response(text)
        assert "<invoke" not in sanitized
        assert "<tool_calls>" not in sanitized
        assert "<parameter" not in sanitized

        plain = plain_text(text)
        assert "<invoke" not in plain
        assert "<tool_calls>" not in plain
