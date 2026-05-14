from __future__ import annotations

import httpx

from donovanagent.config.schema import ActiveProviderConfig, OllamaConfig, SearchConfig
from donovanagent.providers.ollama import OllamaProvider, parse_ollama_tags
from donovanagent.tools.web import TavilySearchProvider


def test_tavily_request_construction() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "answer": "Answer",
                "results": [{"title": "Title", "url": "https://example.com", "content": "Snippet", "score": 0.9}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = TavilySearchProvider(SearchConfig(enabled=True, provider="tavily"), api_key="tvly-test", client=client)
    bundle = provider.search("DonovanAgent", max_results=3)
    assert bundle.answer == "Answer"
    assert bundle.results[0].url == "https://example.com"
    assert '"api_key":"tvly-test"' in str(seen["json"]).replace(" ", "")
    assert '"max_results":3' in str(seen["json"]).replace(" ", "")


def test_ollama_tags_parsing() -> None:
    assert parse_ollama_tags({"models": [{"name": "llama3"}, {"name": "qwen"}]}) == ["llama3", "qwen"]


def test_ollama_model_list_with_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder"}]})

    active = ActiveProviderConfig(active="ollama", model="qwen2.5-coder", base_url="http://127.0.0.1:11434/v1")
    ollama = OllamaConfig(native_url="http://127.0.0.1:11434", model="qwen2.5-coder")
    provider = OllamaProvider(active, ollama, client=httpx.Client(transport=httpx.MockTransport(handler)))
    models = provider.list_models()
    assert models[0].id == "qwen2.5-coder"
