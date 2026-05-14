from __future__ import annotations

from donovanagent.config.schema import ActiveProviderConfig
from donovanagent.providers.openai_compatible import OpenAICompatibleProvider


def test_openai_compatible_payload_includes_tools() -> None:
    config = ActiveProviderConfig(
        active="openai_compatible",
        model="test-model",
        base_url="http://localhost:1234/v1",
        api_key_env="TEST_KEY",
    )
    provider = OpenAICompatibleProvider(config, api_key="")
    payload = provider._build_payload(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}],
    )
    assert payload["model"] == "test-model"
    assert payload["tools"][0]["function"]["name"] == "x"
    assert payload["tool_choice"] == "auto"
