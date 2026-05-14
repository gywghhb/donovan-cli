"""MCP security: trust store, risk classification, and permission integration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from donovanagent.config.paths import DonovanAgentPaths, get_paths
from donovanagent.mcp.config import (
    McpServerConfigModel,
    ConfigScope,
    TrustLevel,
    compute_config_hash,
    mask_secret,
    is_secret_var,
)
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)

# Keywords for risk classification
_READ_KEYWORDS = re.compile(
    r"\b(read|list|get|search|fetch|query|show|display|find|lookup|check|"
    r"peek|inspect|audit|log|monitor|watch|track|index)\b", re.I
)
_WRITE_KEYWORDS = re.compile(
    r"\b(create|update|write|edit|send|post|comment|upload|put|"
    r"configure|set|modify|change|rename|move|copy|add|append)\b", re.I
)
_DESTRUCTIVE_KEYWORDS = re.compile(
    r"\b(delete|remove|drop|truncate|overwrite|erase|clear|purge|destroy|"
    r"kill|stop|terminate|shutdown|restart|reboot|format|wipe)\b", re.I
)
_SHELL_KEYWORDS = re.compile(
    r"\b(shell|exec|run|spawn|invoke|system|cmd|command|bash|sh|zsh|"
    r"powershell|pwsh|terminal|subprocess|os\.system)\b", re.I
)
_NETWORK_KEYWORDS = re.compile(
    r"\b(send|email|message|post|tweet|publish|broadcast|notify|"
    r"webhook|callback|request|fetch|curl|wget|http)\b", re.I
)

# Server categories for baseline risk
_HIGH_RISK_SERVERS = re.compile(
    r"\b(shell|terminal|exec|command|system|process|filesystem|fs|"
    r"database|sql|github|gitlab|admin|deploy|kubernetes|k8s|docker)\b", re.I
)


class McpRiskClassifier:
    """Classify MCP tools by risk level based on name, description, and schema."""

    Risk = str  # "low" | "medium" | "high" | "destructive"

    @classmethod
    def classify(cls, server_name: str, tool_name: str, description: str = "",
                 input_schema: dict[str, Any] | None = None,
                 annotations: dict[str, Any] | None = None) -> tuple[str, str, list[str]]:
        """Classify an MCP tool and return (risk_level, readable_risk, reasons).

        Returns one of: ("low", "read-only"), ("medium", "write"),
        ("high", "shell/command"), ("high", "network"), ("high", "destructive"),
        ("medium", "unknown")
        """
        reasons: list[str] = []
        combined = f"{server_name} {tool_name} {description}"
        schema_json = json.dumps(input_schema or {})

        # Check annotations first
        if annotations:
            if annotations.get("destructive"):
                return "high", "destructive", ["Explicitly marked destructive"]
            if annotations.get("readOnlyHint"):
                return "low", "read-only", ["Annotation indicates read-only"]

        # Check for destructive keywords
        if _DESTRUCTIVE_KEYWORDS.search(combined) or _DESTRUCTIVE_KEYWORDS.search(schema_json):
            reasons.append("Destructive operation keywords detected")
            risk_value = "high"
            risk_label = "destructive"

            # If it's explicitly destructive keyword + server context
            if _HIGH_RISK_SERVERS.search(server_name):
                return "high", "destructive", reasons

            # Check if it also has write/read keywords to refine
            if _WRITE_KEYWORDS.search(combined):
                return "high", "destructive", reasons
            return "high", "destructive", reasons

        # Check for shell/exec keywords
        if _SHELL_KEYWORDS.search(combined) or _SHELL_KEYWORDS.search(schema_json):
            reasons.append("Shell/execution operation")
            return "high", "shell/command", reasons

        # Check for network write keywords
        if _NETWORK_KEYWORDS.search(combined) and _WRITE_KEYWORDS.search(combined):
            reasons.append("External communication")
            return "high", "network", reasons

        # Check for write keywords
        if _WRITE_KEYWORDS.search(combined) or _WRITE_KEYWORDS.search(schema_json):
            reasons.append("Write/mutate operation")
            # Check server context for severity
            if _HIGH_RISK_SERVERS.search(server_name):
                return "high", "write", reasons
            return "medium", "write", reasons

        # Check for read-only keywords
        if _READ_KEYWORDS.search(combined) or _READ_KEYWORDS.search(schema_json):
            return "low", "read-only", []

        # Default: unknown risk, ask user
        return "medium", "unknown", ["Could not determine tool risk level"]

    @classmethod
    def requires_approval(cls, risk_label: str, tool_name: str) -> bool:
        """Determine if a tool requires approval based on risk."""
        if risk_label in ("destructive", "shell/command"):
            return True
        if risk_label in ("write", "network"):
            return True
        if risk_label == "unknown":
            return True
        return False


class McpTrustStore:
    """Persistent trust store for MCP servers.

    Tracks whether the user has trusted or blocked servers.
    Stores a hash of the server config so trust is invalidated
    when the config changes.
    """

    def __init__(self, paths: DonovanAgentPaths | None = None, project_dir: str | None = None) -> None:
        self.paths = paths or get_paths()
        self.project_dir = Path(project_dir).resolve() if project_dir else Path.cwd().resolve()

    @property
    def _trust_file(self) -> Path:
        return self.paths.config_dir / "mcp_trust.json"

    @property
    def _project_trust_file(self) -> Path:
        return self.project_dir / ".donovan" / "mcp_trust.local.json"

    def _load_trust(self) -> dict[str, Any]:
        data: dict[str, Any] = {"servers": {}, "project_servers": {}}
        # Global trust
        if self._trust_file.exists():
            try:
                data.update(json.loads(self._trust_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        # Project-level trust (overrides global)
        if self._project_trust_file.exists():
            try:
                project_data = json.loads(self._project_trust_file.read_text(encoding="utf-8"))
                data.setdefault("project_servers", {}).update(
                    project_data.get("project_servers", {})
                )
            except (json.JSONDecodeError, OSError):
                pass
        return data

    def _save_trust(self, data: dict[str, Any]) -> None:
        self._trust_file.parent.mkdir(parents=True, exist_ok=True)
        self._trust_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _save_project_trust(self, data: dict[str, Any]) -> None:
        path = self._project_trust_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def get_trust(self, name: str, scope: ConfigScope) -> TrustLevel | None:
        """Get the stored trust level for a server. Returns None if no decision stored.
        Config-change detection is handled separately by has_config_changed()."""
        data = self._load_trust()
        servers = data.get("servers", {})
        if name in servers:
            return servers[name].get("trust")

        if scope == "project":
            project_servers = data.get("project_servers", {})
            if name in project_servers:
                return project_servers[name].get("trust")

        return None

    def set_trust(self, name: str, trust: TrustLevel, scope: ConfigScope, config_hash: str) -> None:
        """Store a trust decision for a server."""
        data = self._load_trust()

        entry = {"trust": trust, "hash": config_hash}

        if scope == "user":
            data.setdefault("servers", {})[name] = entry
            self._save_trust(data)
        else:
            # Project/local scope — store in project trust file
            data.setdefault("project_servers", {})[name] = entry
            self._save_project_trust(data)

        logger.info("MCP trust set: %s = %s (scope: %s)", name, trust, scope)

    def is_trusted(self, name: str, config: McpServerConfigModel, scope: ConfigScope) -> bool:
        """Check if a server is trusted (or blocked). Returns True if trusted/ask."""
        if config.trust == "trusted":
            return True
        if config.trust == "blocked":
            return False

        stored = self.get_trust(name, scope)
        if stored == "blocked":
            return False
        if stored == "trusted":
            return True

        # "ask" means we need to prompt
        return False  # caller should show trust prompt

    def is_blocked(self, name: str, config: McpServerConfigModel, scope: ConfigScope) -> bool:
        """Check if a server is explicitly blocked."""
        if config.trust == "blocked":
            return True
        return self.get_trust(name, scope) == "blocked"

    def has_config_changed(self, name: str, config: McpServerConfigModel, scope: ConfigScope) -> bool:
        """Check if a server's effective config has changed since it was trusted."""
        data = self._load_trust()
        servers = data.get("servers", {})
        project_servers = data.get("project_servers", {})

        stored_hash = None
        if scope == "user" and name in servers:
            stored_hash = servers[name].get("hash")
        elif name in project_servers:
            stored_hash = project_servers[name].get("hash")

        if stored_hash is None:
            return False  # never trusted

        current_hash = compute_config_hash(config.effective_config_dict())
        return current_hash != stored_hash

    def reset_project_choices(self) -> None:
        """Reset all project-level trust decisions."""
        if self._project_trust_file.exists():
            self._project_trust_file.unlink()
            logger.info("MCP project trust choices reset")
