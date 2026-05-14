from __future__ import annotations

from donovanagent.config.schema import DonovanAgentConfig
from donovanagent.providers.anthropic_provider import AnthropicProvider
from donovanagent.providers.base import LLMProvider
from donovanagent.providers.ollama import OllamaProvider
from donovanagent.providers.openai_compatible import OpenAICompatibleProvider, OpenAIProvider
from donovanagent.utils.errors import ProviderError

# OpenAI-compatible providers keyed by active name Ã¢â€ â€™ providers config attribute name
_COMPATIBLE = {
    "deepseek": "deepseek",
    "lmstudio": "lmstudio",
    "qwen": "qwen",
    "openai_compatible": "custom",
}

# Per-provider max output token ceilings (None = use config value)
_MAX_TOKENS: dict[str, int] = {
    "openai": 16384,
    "anthropic": 8192,
    "deepseek": 8192,
    "qwen": 8192,
    "lmstudio": 8192,
    "openai_compatible": 8192,
    "ollama": 8192,
}


def build_provider(config: DonovanAgentConfig) -> LLMProvider:
    active = config.provider.active
    timeout = max(config.provider.timeout_seconds, 120)
    max_tokens = max(config.provider.max_tokens, _MAX_TOKENS.get(active, 8192))

    if active == "openai":
        return OpenAIProvider(
            config.provider,
            temperature=config.provider.temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout,
            stream=config.provider.stream,
        )

    if active in _COMPATIBLE:
        cfg_attr = _COMPATIBLE[active]
        named = getattr(config.providers, cfg_attr)
        from donovanagent.config.schema import ActiveProviderConfig
        active_cfg = ActiveProviderConfig(
            active=active,
            base_url=named.base_url,
            api_key_env=named.api_key_env,
            model=named.model or config.provider.model,
            temperature=config.provider.temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout,
            stream=config.provider.stream,
        )
        return OpenAICompatibleProvider(
            active_cfg,
            provider_name=active,
            temperature=config.provider.temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout,
            stream=config.provider.stream,
        )

    if active == "anthropic":
        from donovanagent.config.schema import ActiveProviderConfig
        cfg = ActiveProviderConfig(
            active="anthropic",
            base_url=config.providers.anthropic.base_url,
            api_key_env=config.providers.anthropic.api_key_env,
            model=config.providers.anthropic.model or config.provider.model,
            max_tokens=max_tokens,
            temperature=config.provider.temperature,
            timeout_seconds=timeout,
        )
        return AnthropicProvider(cfg, timeout_seconds=timeout)

    if active == "ollama":
        return OllamaProvider(config.provider, config.providers.ollama)

    raise ProviderError("No LLM provider configured. Run DonovanAgent setup or DonovanAgent model set.")
