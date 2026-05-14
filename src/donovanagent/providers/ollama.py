from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from donovanagent.config.schema import ActiveProviderConfig, OllamaConfig
from donovanagent.providers.models import ModelInfo
from donovanagent.providers.openai_compatible import OpenAICompatibleProvider
from donovanagent.utils.errors import ProviderError


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"

    def __init__(
        self,
        active_config: ActiveProviderConfig,
        ollama_config: OllamaConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            active_config,
            provider_name="ollama",
            temperature=active_config.temperature,
            max_tokens=active_config.max_tokens,
            timeout_seconds=active_config.timeout_seconds,
            stream=active_config.stream,
            api_key="",
            client=client,
        )
        self.native_url = ollama_config.native_url.rstrip("/") + "/"

    @property
    def tags_url(self) -> str:
        return urljoin(self.native_url, "api/tags")

    def list_models(self) -> list[ModelInfo]:
        try:
            response = self.client.get(self.tags_url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Ollama HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama is not reachable: {exc}") from exc
        data = response.json()
        return [
            ModelInfo(id=str(item.get("name") or ""), provider="ollama", details=item)
            for item in data.get("models", [])
        ]

    def validate_connection(self) -> tuple[bool, str]:
        try:
            models = self.list_models()
        except Exception as exc:
            return False, str(exc)
        if self.model and all(model.id != self.model for model in models):
            return False, f"Ollama reachable, but model '{self.model}' is not installed"
        return True, f"ok ({len(models)} installed models)"


def parse_ollama_tags(data: dict[str, Any]) -> list[str]:
    return [str(model.get("name")) for model in data.get("models", []) if model.get("name")]
