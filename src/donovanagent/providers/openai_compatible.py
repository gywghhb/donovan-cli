from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from donovanagent.config.schema import ActiveProviderConfig, NamedProviderConfig
from donovanagent.providers.base import LLMProvider
from donovanagent.providers.models import ChatResponse, ModelInfo, ToolCall
from donovanagent.utils.errors import ProviderError


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        config: ActiveProviderConfig | NamedProviderConfig,
        *,
        provider_name: str = "openai_compatible",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_seconds: int = 60,
        stream: bool = True,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = provider_name
        self.base_url = config.base_url.rstrip("/") + "/"
        self.model = config.model
        self.api_key_env = config.api_key_env
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.stream = stream
        self.api_key = api_key if api_key is not None else os.getenv(self.api_key_env, "")
        self.client = client or httpx.Client(timeout=timeout_seconds)

    @property
    def chat_url(self) -> str:
        return urljoin(self.base_url, "chat/completions")

    @property
    def models_url(self) -> str:
        return urljoin(self.base_url, "models")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
        tool_choice: str | dict[str, Any] | None = "auto",
    ) -> dict[str, Any]:
        if not self.model:
            raise ProviderError("No model configured for active provider")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        return payload

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)),
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
        payload = self._build_payload(messages, tools, stream=False, tool_choice=tool_choice)
        try:
            response = self.client.post(self.chat_url, headers=self._headers(), json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Provider HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Request timed out after {self.timeout_seconds}s. "
                "The model may still be loading â€” try again, or increase timeout_seconds in your config."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Provider request failed: {exc}") from exc
        data = response.json()
        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Malformed provider response: {data}") from exc
        usage = data.get("usage") or {}
        tool_calls = parse_tool_calls(message.get("tool_calls") or [])
        return ChatResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            raw=data,
            finish_reason=choice.get("finish_reason"),
            reasoning_content=message.get("reasoning_content") or "",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[str]:
        payload = self._build_payload(messages, tools, stream=True, tool_choice="auto")
        try:
            with self.client.stream("POST", self.chat_url, headers=self._headers(), json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
        except httpx.HTTPError as exc:
            raise ProviderError(f"Provider stream failed: {exc}") from exc

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self.client.get(self.models_url, headers=self._headers())
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Provider HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Provider request failed: {exc}") from exc
        data = response.json()
        return [
            ModelInfo(id=str(item.get("id") or item.get("name") or ""), provider=self.name, details=item)
            for item in data.get("data", [])
        ]

    def validate_connection(self) -> tuple[bool, str]:
        if self.api_key_env and not self.api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            return False, f"Missing API key in environment variable {self.api_key_env}"
        try:
            models = self.list_models()
        except Exception as exc:
            return False, str(exc)
        return True, f"ok ({len(models)} models visible)"


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"

    def __init__(self, config: ActiveProviderConfig, **kwargs: Any) -> None:
        super().__init__(config, provider_name="openai", **kwargs)


def parse_tool_calls(items: list[dict[str, Any]]) -> list[ToolCall]:
    parsed: list[ToolCall] = []
    for item in items:
        function = item.get("function") or {}
        name = function.get("name")
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            arguments = {"_raw": raw_arguments}
        if name:
            parsed.append(ToolCall(id=str(item.get("id") or name), name=str(name), arguments=arguments))
    return parsed
