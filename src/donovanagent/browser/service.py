from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

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
        self._minimized = False
        self._connected_endpoint: str | None = None

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
            self._connected_endpoint = cdp
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
                if cdp_endpoint or self._browser_type_name == "cdp":
                    self._context = browser.contexts[0] if browser.contexts else browser.new_context()
                    pages = self._context.pages
                    self._page = pages[-1] if pages else self._context.new_page()
                else:
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
            self._minimized = False
        except Exception as exc:
            raise ProviderError(f"Failed to navigate to {url}: {exc}")

    def discover_debug_endpoints(self) -> list[str]:
        """Return reachable local Chromium CDP endpoints."""
        candidates: list[str] = []
        browser_cfg = self.config.browser if hasattr(self.config, "browser") else None
        configured = browser_cfg.cdp_endpoint if browser_cfg else None
        if configured:
            candidates.append(configured.rstrip("/"))
        for port in (9222, 9223, 9224, 9225, 9333):
            candidates.append(f"http://127.0.0.1:{port}")
            candidates.append(f"http://localhost:{port}")
        seen: set[str] = set()
        reachable: list[str] = []
        for endpoint in candidates:
            if endpoint in seen:
                continue
            seen.add(endpoint)
            try:
                response = httpx.get(f"{endpoint}/json/version", timeout=0.5)
                if response.status_code == 200:
                    reachable.append(endpoint)
            except httpx.HTTPError:
                continue
        return reachable

    def connect_existing(self, cdp_endpoint: str | None = None, tab: int | str | None = None) -> None:
        """Attach to an existing browser debug endpoint and select an existing tab."""
        endpoint = cdp_endpoint
        if not endpoint:
            endpoints = self.discover_debug_endpoints()
            endpoint = endpoints[0] if endpoints else None
        if not endpoint:
            raise ProviderError(
                "No debuggable browser found. Start Chrome or Edge with "
                "--remote-debugging-port=9222, then use /browser connect. "
                "Safari and browsers without a remote automation endpoint cannot expose active tabs to Donovan."
            )
        browser = self._get_browser_type("auto", endpoint)
        self._browser = browser
        self._context = browser.contexts[0] if browser.contexts else browser.new_context()
        pages = self._context.pages
        if not pages:
            raise ProviderError("Connected to browser, but no open tabs were exposed.")
        self._page = self._select_page(pages, tab)
        self._minimized = False

    def _select_page(self, pages: list[Any], tab: int | str | None) -> Any:
        if tab is None:
            return pages[-1]
        if isinstance(tab, int):
            index = max(0, min(tab, len(pages) - 1))
            return pages[index]
        lowered = tab.lower()
        for page in pages:
            try:
                title = page.title()
                url = page.url
            except Exception:
                title = ""
                url = ""
            if lowered in title.lower() or lowered in url.lower():
                return page
        raise ProviderError(f"No existing browser tab matched: {tab}")

    def list_tabs(self) -> list[dict[str, str]]:
        if not self._context:
            self.connect_existing()
        if not self._context:
            return []
        tabs: list[dict[str, str]] = []
        for index, page in enumerate(self._context.pages):
            try:
                title = page.title()
            except Exception:
                title = ""
            tabs.append({"index": str(index), "title": title, "url": page.url})
        return tabs

    def use_tab(self, tab: int | str) -> None:
        if not self._context:
            self.connect_existing()
        if not self._context or not self._context.pages:
            raise ProviderError("No browser tabs are available.")
        self._page = self._select_page(self._context.pages, tab)
        try:
            self._page.bring_to_front()
        except Exception:
            pass
        self._minimized = False

    @property
    def is_minimized(self) -> bool:
        return self._minimized

    def minimize(self) -> None:
        """Minimize the browser window without closing the page.

        Playwright exposes real window minimization through Chromium's CDP.
        For browsers/CDP targets that do not support it, fall back to moving
        focus away from the page and remember the minimized state.
        """
        if not self._page:
            return
        try:
            session = self._context.new_cdp_session(self._page) if self._context else None
            if session:
                window = session.send("Browser.getWindowForTarget")
                window_id = window.get("windowId")
                if window_id is not None:
                    session.send(
                        "Browser.setWindowBounds",
                        {"windowId": window_id, "bounds": {"windowState": "minimized"}},
                    )
                    self._minimized = True
                    return
        except Exception as exc:
            logger.debug("Native browser minimize failed: %s", exc)
        try:
            self._page.evaluate("() => window.blur()")
        except Exception:
            pass
        self._minimized = True

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
            self._minimized = False
            self._connected_endpoint = None

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
