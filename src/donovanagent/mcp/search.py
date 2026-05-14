"""MCP tool search for deferred loading.

When many MCP tools exist (> deferToolsAbove), Donovan uses a local
BM25-style lexical search so the model can find relevant tools without
flooding context with all tool schemas.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from donovanagent.mcp.registry import McpToolRegistry, _mcp_tool_name
from donovanagent.mcp.client import McpToolInfo
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SearchableTool:
    """A searchable MCP tool with indexed terms."""
    full_name: str
    original_name: str
    server_name: str
    description: str
    argument_names: list[str] = field(default_factory=list)
    argument_descriptions: list[str] = field(default_factory=list)

    def all_text(self) -> str:
        """Return all text for indexing."""
        parts = [
            self.full_name,
            self.original_name,
            self.server_name,
            self.description,
            " ".join(self.argument_names),
            " ".join(self.argument_descriptions),
        ]
        return " ".join(parts)


class McpToolSearch:
    """Searchable index of MCP tools.

    Uses a simple BM25-inspired ranking over tokenized text fields.
    Designed to be replaced with embeddings when needed.
    """

    def __init__(self) -> None:
        self._tools: list[SearchableTool] = []
        self._doc_freq: Counter[str] = Counter()
        self._total_docs: int = 0
        self._avg_doc_len: float = 0.0
        self._k1: float = 1.5
        self._b: float = 0.75
        self._dirty: bool = False

    def rebuild(self, registry: McpToolRegistry) -> None:
        """Rebuild the search index from the current registry."""
        self._tools = []
        for full_name, info in registry.get_all_tool_infos():
            parsed = full_name.split("__", 2)
            server_name = parsed[1] if len(parsed) >= 2 else ""
            original_name = info.name

            arg_names: list[str] = []
            arg_descs: list[str] = []
            if info.inputSchema:
                props = info.inputSchema.get("properties", {})
                for prop_name, prop_schema in props.items():
                    arg_names.append(prop_name)
                    if isinstance(prop_schema, dict):
                        arg_descs.append(prop_schema.get("description", ""))

            self._tools.append(SearchableTool(
                full_name=full_name,
                original_name=original_name,
                server_name=server_name,
                description=info.description or "",
                argument_names=arg_names,
                argument_descriptions=arg_descs,
            ))

        self._build_index()

    def _build_index(self) -> None:
        """Build the inverted index."""
        self._doc_freq = Counter()
        self._total_docs = len(self._tools)
        if self._total_docs == 0:
            self._avg_doc_len = 0.0
            return

        total_len = 0
        for tool in self._tools:
            terms = set(self._tokenize(tool.all_text()))
            for term in terms:
                self._doc_freq[term] += 1
            total_len += len(self._tokenize(tool.all_text()))

        self._avg_doc_len = total_len / self._total_docs
        self._dirty = False

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into lowercased terms."""
        text = text.lower()
        # Split on non-alphanumeric chars, keep underscores
        tokens = re.findall(r"[a-z0-9_]+", text)
        return tokens

    def search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """Search for MCP tools matching the query.

        Returns list of (full_name, score) sorted by relevance.
        """
        if self._dirty or self._total_docs == 0:
            return []

        query_terms = self._tokenize(query)
        if not query_terms:
            return []

        # Score each document
        scores: list[tuple[int, float]] = []
        for i, tool in enumerate(self._tools):
            doc_terms = self._tokenize(tool.all_text())
            doc_len = len(doc_terms)
            term_counts = Counter(doc_terms)

            score = 0.0
            for term in query_terms:
                tf = term_counts.get(term, 0)
                if tf == 0:
                    continue
                df = self._doc_freq.get(term, 1)
                idf = math.log(
                    (self._total_docs - df + 0.5) / (df + 0.5) + 1.0
                )
                numerator = tf * (self._k1 + 1)
                denominator = tf + self._k1 * (
                    1 - self._b + self._b * doc_len / self._avg_doc_len
                )
                score += idf * numerator / denominator

            # Boost exact name matches
            if query.lower() in tool.original_name.lower():
                score *= 2.0
            if query.lower() in tool.server_name.lower():
                score *= 1.5

            if score > 0:
                scores.append((i, score))

        # Sort by score descending
        scores.sort(key=lambda x: -x[1])
        return [(self._tools[i].full_name, s) for i, s in scores[:limit]]
