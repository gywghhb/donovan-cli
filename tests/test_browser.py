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


def test_browser_service_minimize_without_open_browser() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.browser.service import BrowserService

    service = BrowserService(DonovanAgentConfig())
    service.minimize()
    assert service.is_minimized is False


def test_browser_connect_existing_guides_when_no_debug_browser() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.browser.service import BrowserService
    from donovanagent.utils.errors import ProviderError

    service = BrowserService(DonovanAgentConfig())
    service.discover_debug_endpoints = lambda: []  # type: ignore[method-assign]
    try:
        service.connect_existing()
    except ProviderError as exc:
        message = str(exc)
    else:
        raise AssertionError("connect_existing should fail without a debuggable browser")
    assert "--remote-debugging-port=9222" in message
    assert "Safari" in message


def test_browser_tools_include_existing_tab_workflow() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.tools.registry import build_default_registry

    registry = build_default_registry(DonovanAgentConfig())
    names = {tool.name for tool in registry.list()}
    assert "browser_connect_existing" in names
    assert "browser_list_tabs" in names
    assert "browser_use_tab" in names
    assert "browser_minimize" in names


def test_browser_companion_extension_files(tmp_path: Path) -> None:
    from donovanagent.browser.companion import BrowserCompanionService

    service = BrowserCompanionService(tmp_path)
    extension_dir = service.install_extension_files()
    assert (extension_dir / "manifest.json").exists()
    assert (extension_dir / "background.js").exists()
    assert (extension_dir / "content.js").exists()
    assert "edge://extensions" in service.setup_instructions()


def test_browser_tools_include_companion_workflow() -> None:
    from donovanagent.config.schema import DonovanAgentConfig
    from donovanagent.tools.registry import build_default_registry

    registry = build_default_registry(DonovanAgentConfig())
    names = {tool.name for tool in registry.list()}
    assert "browser_companion_setup" in names
    assert "browser_companion_snapshot" in names
    assert "browser_companion_click" in names
    assert "browser_companion_type" in names
