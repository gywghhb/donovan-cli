from __future__ import annotations

from pathlib import Path

from donovanagent.browser.models import BrowserSession


def test_browser_session_defaults() -> None:
    session = BrowserSession()
    assert session.id == ""
    assert session.url is None
    assert session.browser_type == "chromium"
    assert session.status == "closed"


def test_browser_session_with_url() -> None:
    session = BrowserSession(url="https://example.com", browser_type="chromium")
    assert session.url == "https://example.com"
    assert session.browser_type == "chromium"
    assert session.status == "closed"


def test_browser_session_lifecycle() -> None:
    session = BrowserSession()
    session.status = "open"
    assert session.status == "open"
    session.status = "closed"
    assert session.status == "closed"


def test_browser_config_defaults() -> None:
    from donovanagent.config.schema import DonovanAgentConfig, BrowserConfig

    config = DonovanAgentConfig()
    assert config.browser.enabled is True
    assert config.browser.headless is False
    assert config.browser.default == "auto"
    assert config.browser.timeout_seconds == 30


def test_browser_service_initialization() -> None:
    """Test BrowserService init without Playwright (will skip browser launch)."""
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.browser.service import BrowserService

    config = DonovanAgentConfig()
    service = BrowserService(config)

    # Service initializes but browser is not open
    assert service.is_open is False
