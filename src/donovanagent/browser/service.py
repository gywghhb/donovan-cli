from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from donovanagent.utils.errors import ProviderError
from donovanagent.utils.logging import get_logger

logger = get_logger(__name__)


class BrowserService:
    """Manages browser automation via Playwright with CDP support."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self._browser = None
        self._page = None
        self._context = None
        self._playwright = None
        self._session_id: str | None = None
        self._browser_type_name: str = "chromium"

    def _ensure_playwright(self) -> None:
        if self._playwright is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
            self._playwright_sync = sync_playwright
        except ImportError:
            raise ProviderError(
                "Browser automation requires Playwright. "
                "Install: pip install playwright && playwright install"
            )

    def _get_browser_type(self, browser_type: str, cdp_endpoint: str | None = None):
        self._ensure_playwright()
        with self._playwright_sync() as pw:
            pw.stop()  # Just checking availability
        # We'll use the import directly
        import playwright.sync_api as pw_api

        browser_cfg = self.config.browser if hasattr(self.config, "browser") else None

        # Runtime CDP endpoint takes priority, then config CDP
        cdp = cdp_endpoint or (browser_cfg.cdp_endpoint if browser_cfg else None)
        if cdp:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            self._playwright = playwright
            browser = playwright.chromium.connect_over_cdp(cdp)
            self._browser_type_name = "cdp"
            return browser

        custom_path = browser_cfg.custom_executable_path if browser_cfg else None
        headless = browser_cfg.headless if browser_cfg else False

        bt = browser_type.lower()
        if bt == "auto" or bt == "chromium":
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            self._playwright = playwright
            self._browser_type_name = "chromium"
            return playwright.chromium.launch(
                headless=headless,
                executable_path=custom_path,
            )
        elif bt in ("chrome", "edge"):
            channel = "chrome" if bt == "chrome" else "msedge"
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            self._playwright = playwright
            self._browser_type_name = bt
            return playwright.chromium.launch(
                channel=channel,
                headless=headless,
                executable_path=custom_path,
            )
        elif bt == "webkit":
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            self._playwright = playwright
            self._browser_type_name = "webkit"
            return playwright.webkit.launch(
                headless=headless,
                executable_path=custom_path,
            )
        raise ProviderError(f"Unsupported browser type: {browser_type}")

    @property
    def is_open(self) -> bool:
        return self._page is not None

    @property
    def browser_type(self) -> str:
        return self._browser_type_name

    def open(self, url: str, browser_type: str = "auto", cdp_endpoint: str | None = None) -> None:
        """Open a URL in the browser."""
        if self._page is None:
            try:
                browser = self._get_browser_type(browser_type, cdp_endpoint)
                self._browser = browser
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    user_agent="DonovanAgent/1.0",
                )
                self._context = ctx
                self._page = ctx.new_page()
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"Failed to launch browser: {exc}")
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            raise ProviderError(f"Failed to navigate to {url}: {exc}")

    def close(self) -> None:
        """Close the browser and clean up asyncio state."""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception as exc:
            logger.debug("Error closing browser: %s", exc)
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

        # Playwright's sync wrapper sets a running loop internally but
        # doesn't clear it on shutdown. If we don't reset it here,
        # prompt_toolkit's asyncio.run() refuses on the next turn.
        # Stray task callbacks from Playwright can fire after cleanup
        # and raise RuntimeError — suppress those quietly.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                asyncio._set_running_loop(None)
                asyncio.set_event_loop(asyncio.new_event_loop())
            except RuntimeError:
                pass

    def screenshot(self, path: str | None = None) -> str:
        if not self._page:
            raise ProviderError("Browser is not open. Use browser_open first.")
        if not path:
            cfg = self.config.browser if hasattr(self.config, "browser") else None
            screenshot_dir = cfg.screenshot_dir if cfg else ".DonovanAgent/browser/screenshots"
            sp = Path(screenshot_dir)
            sp.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            path = str(sp / f"screenshot_{stamp}.png")
        self._page.screenshot(path=path, full_page=True)
        return path

    def click(self, selector: str) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        try:
            self._page.click(selector, timeout=10000)
        except Exception as exc:
            raise ProviderError(f"Failed to click '{selector}': {exc}")

    def type_text(self, selector: str, text: str) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        try:
            self._page.fill(selector, text, timeout=10000)
        except Exception as exc:
            raise ProviderError(f"Failed to type into '{selector}': {exc}")

    def press(self, key: str) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        self._page.keyboard.press(key)

    def get_text(self) -> str:
        if not self._page:
            raise ProviderError("Browser is not open.")
        return self._page.inner_text("body") or ""

    def get_html(self) -> str:
        if not self._page:
            raise ProviderError("Browser is not open.")
        return self._page.content()

    def current_url(self) -> str:
        if not self._page:
            raise ProviderError("Browser is not open.")
        return self._page.url

    def extract_links(self) -> list[dict[str, str]]:
        if not self._page:
            raise ProviderError("Browser is not open.")
        links = self._page.eval_on_selector_all(
            "a",
            "els => els.map(el => ({ text: el.innerText.trim(), href: el.href }))",
        )
        return [{"text": l.get("text", ""), "href": l.get("href", "")} for l in links]

    def back(self) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        self._page.go_back()

    def forward(self) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        self._page.go_forward()

    def reload(self) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        self._page.reload()

    def evaluate(self, script: str) -> Any:
        if not self._page:
            raise ProviderError("Browser is not open.")
        return self._page.evaluate(script)

    def wait_for_selector(self, selector: str, timeout: int = 30000) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        self._page.wait_for_selector(selector, timeout=timeout)

    def wait_for_timeout(self, ms: int) -> None:
        if not self._page:
            raise ProviderError("Browser is not open.")
        self._page.wait_for_timeout(ms)
