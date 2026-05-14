from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from donovanagent.config.schema import ActiveProviderConfig
from donovanagent.providers.base import LLMProvider
from donovanagent.providers.models import ChatResponse, ModelInfo, ToolCall
from donovanagent.utils.errors import ProviderError


_BASE_URL = "https://api.anthropic.com/v1"
_API_VERSION = "2023-06-01"

_KNOWN_MODELS: list[str] = []


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split system prompt and convert tool messages to Anthropic format."""
    system = ""
    result: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            system = str(m.get("content") or "")
            continue
        if role == "tool":
            # Merge consecutive tool results into one user message
            block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""), "content": str(m.get("content") or "")}
            if result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], list):
                result[-1]["content"].append(block)
            else:
                result.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            raw_calls = (m.get("tool_calls") or [])
            if raw_calls:
                content_blocks: list[dict[str, Any]] = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": str(m["content"])})
                for call in raw_calls:
                    if isinstance(call, dict):
                        fn = call.get("function") or {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": str(call.get("id") or fn.get("name") or "call"),
                            "name": str(fn.get("name") or ""),
                            "input": fn.get("arguments") or {},
                        })
                result.append({"role": "assistant", "content": content_blocks})
                continue
        result.append({"role": role, "content": m.get("content") or ""})
    return system, result


def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        fn = t.get("function") or t
        out.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _parse_response(data: dict[str, Any]) -> ChatResponse:
    text = ""
    tool_calls: list[ToolCall] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text += block.get("text") or ""
        elif block.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                id=str(block.get("id") or block.get("name") or ""),
                name=str(block.get("name") or ""),
                arguments=block.get("input") or {},
            ))
    usage = data.get("usage") or {}
    return ChatResponse(
        content=text,
        tool_calls=tool_calls,
        raw=data,
        finish_reason=data.get("stop_reason"),
        prompt_tokens=usage.get("input_tokens", 0),
        completion_tokens=usage.get("output_tokens", 0),
    )


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, config: ActiveProviderConfig, *, timeout_seconds: int = 120) -> None:
        self.model = config.model or "anthropic-default"
        self.api_key_env = config.api_key_env or "ANTHROPIC_API_KEY"
        self.api_key = os.getenv(self.api_key_env, "")
        self.max_tokens = config.max_tokens
        self.temperature = config.temperature
        self.timeout_seconds = timeout_seconds
        self.client = httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
    ) -> ChatResponse:
        system, converted = _to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _to_anthropic_tools(tools)
            payload["tool_choice"] = {"type": "auto"}
        try:
            resp = self.client.post(f"{_BASE_URL}/messages", headers=self._headers(), json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Anthropic HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Anthropic request timed out (>{self.timeout_seconds}s). Try a faster model or increase timeout.") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc
        return _parse_response(resp.json())

    def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Iterator[str]:
        system, converted = _to_anthropic_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": converted,
            "stream": True,
        }
        if system:
            payload["system"] = system
        try:
            with self.client.stream("POST", f"{_BASE_URL}/messages", headers=self._headers(), json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        import json
                        try:
                            data = json.loads(line[6:])
                        except Exception:
                            continue
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                yield delta.get("text") or ""
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic stream failed: {exc}") from exc

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=m, provider="anthropic") for m in _KNOWN_MODELS]

    def validate_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"Missing API key â€” set env var {self.api_key_env}"
        try:
            resp = self.client.get(f"{_BASE_URL}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            count = len(data.get("data") or [])
            return True, f"ok ({count} models)"
        except Exception as exc:
            return False, str(exc)
