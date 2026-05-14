from __future__ import annotations

import os

from donovanagent.config.schema import DonovanAgentConfig


def is_web_search_enabled(config: DonovanAgentConfig | None = None) -> bool:
    """Check whether web search (Tavily) is actually usable.

    Returns ``True`` only when search is enabled in the configuration
    *and* a non-empty API key is set in the environment.
    """
    if config is None:
        return bool(os.environ.get("TAVILY_API_KEY", "").strip())

    if not config.search.enabled or config.search.provider == "none":
        return False

    api_key = os.environ.get(config.search.tavily_api_key_env, "")
    return bool(api_key.strip())
