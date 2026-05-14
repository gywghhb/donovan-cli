from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from donovanagent.config.schema import SearchConfig
from donovanagent.tools.base import ToolExecutionContext, ToolResult
from donovanagent.utils.errors import ProviderError


@dataclass
class SearchResult:
    title: str
    url: str
    content: str
    score: float | None = None
    published_date: str | None = None
    raw_content: str | None = None


@dataclass
class SearchResultBundle:
    query: str
    answer: str | None
    results: list[SearchResult]
    provider: str = "tavily"

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.answer,
            "provider": self.provider,
            "results": [result.__dict__ for result in self.results],
        }


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 5) -> SearchResultBundle: ...

    def validate_connection(self) -> tuple[bool, str]: ...


class TavilySearchProvider:
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        config: SearchConfig,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.api_key = api_key if api_key is not None else os.getenv(config.tavily_api_key_env, "")
        self.client = client or httpx.Client(timeout=30)

    def _payload(self, query: str, max_results: int) -> dict[str, Any]:
        return {
            "api_key": self.api_key,
            "query": query,
            "search_depth": self.config.search_depth,
            "max_results": max_results,
            "include_answer": self.config.include_answer,
            "include_raw_content": self.config.include_raw_content,
        }

    def search(self, query: str, max_results: int = 5) -> SearchResultBundle:
        if not self.api_key:
            raise ProviderError(
                f"Tavily is not configured. Set {self.config.tavily_api_key_env} in DonovanAgent .env."
            )
        payload = self._payload(query, max_results)
        try:
            response = self.client.post(self.endpoint, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Tavily API error {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Tavily request failed: {exc}") from exc
        data = response.json()
        results = [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                content=str(item.get("content") or item.get("snippet") or ""),
                score=item.get("score"),
                published_date=item.get("published_date"),
                raw_content=item.get("raw_content"),
            )
            for item in data.get("results", [])
        ]
        return SearchResultBundle(query=query, answer=data.get("answer"), results=results)

    def validate_connection(self) -> tuple[bool, str]:
        try:
            bundle = self.search("DonovanAgent connectivity test", max_results=1)
        except Exception as exc:
            return False, str(exc)
        return True, f"ok ({len(bundle.results)} result)"


def web_search(ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
    if not ctx.config.search.enabled or ctx.config.search.provider != "tavily":
        return ToolResult(
            False,
            "Web search is not configured. Run DonovanAgent setup or set search.enabled true and configure Tavily.",
        )
    max_results = int(args.get("max_results") or ctx.config.search.max_results)
    provider = TavilySearchProvider(ctx.config.search)
    bundle = provider.search(str(args["query"]), max_results=max_results)
    ctx.db.add_audit(
        "web_search",
        "agent",
        session_id=ctx.session_id,
        approved=True,
        details={"query": bundle.query, "result_count": len(bundle.results)},
    )
    lines: list[str] = []
    if bundle.answer:
        lines.extend(["Answer:", bundle.answer, ""])
    for index, result in enumerate(bundle.results, start=1):
        lines.append(f"{index}. {result.title}")
        lines.append(f"   {result.url}")
        lines.append(f"   {result.content}")
        if result.published_date:
            lines.append(f"   Published: {result.published_date}")
    return ToolResult(True, "\n".join(lines), bundle.to_dict())
