from __future__ import annotations

from typing import Any

from donovanagent.browser.service import BrowserService
from donovanagent.tools.base import ToolExecutionContext, ToolResult
from donovanagent.utils.errors import ProviderError


class BrowserToolWrapper:
    """Wraps BrowserService methods as callable tool handlers."""

    def __init__(self, service: BrowserService) -> None:
        self.service = service

    def open(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        url = str(args.get("url", ""))
        browser = str(args.get("browser", "auto"))
        if not url:
            return ToolResult(False, "URL is required")
        try:
            self.service.open(url, browser_type=browser)
            return ToolResult(True, f"Opened {url}", {"url": url})
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def close(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        self.service.close()
        return ToolResult(True, "Browser closed")

    def snapshot(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        try:
            text = self.service.get_text()
            url = self.service.current_url()
            return ToolResult(True, text, {"url": url})
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def screenshot(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        path = args.get("path")
        try:
            result = self.service.screenshot(path)
            return ToolResult(True, f"Screenshot saved to {result}", {"path": str(result)})
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def click(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        selector = str(args.get("selector", ""))
        if not selector:
            return ToolResult(False, "Selector is required")
        try:
            self.service.click(selector)
            return ToolResult(True, f"Clicked {selector}")
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def type_text(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        selector = str(args.get("selector", ""))
        text = str(args.get("text", ""))
        if not selector:
            return ToolResult(False, "Selector is required")
        try:
            self.service.type_text(selector, text)
            return ToolResult(True, f"Typed into {selector}")
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def press_key(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        key = str(args.get("key", ""))
        if not key:
            return ToolResult(False, "Key is required")
        try:
            self.service.press(key)
            return ToolResult(True, f"Pressed {key}")
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def extract_links(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        try:
            links = self.service.extract_links()
            return ToolResult(True, "\n".join(f"- {link}" for link in links), {"links": links})
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def extract_text(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        try:
            text = self.service.get_text()
            return ToolResult(True, text)
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def get_html(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        try:
            html = self.service.get_html()
            return ToolResult(True, html[:5000])
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def get_url(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        try:
            url = self.service.current_url()
            return ToolResult(True, url, {"url": url})
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def navigate(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        action = str(args.get("direction", "back"))
        try:
            if action == "back":
                self.service.back()
            elif action == "forward":
                self.service.forward()
            elif action == "reload":
                self.service.reload()
            return ToolResult(True, f"Navigated {action}")
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def evaluate(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        script = str(args.get("script", ""))
        if not script:
            return ToolResult(False, "Script is required")
        try:
            result = self.service.evaluate(script)
            return ToolResult(True, str(result), {"result": result})
        except ProviderError as exc:
            return ToolResult(False, str(exc))

    def wait_for(self, ctx: ToolExecutionContext, args: dict[str, Any]) -> ToolResult:
        selector = args.get("selector")
        timeout = int(args.get("timeout", 30)) * 1000
        try:
            if selector:
                self.service.wait_for_selector(str(selector), timeout)
                return ToolResult(True, f"Element appeared: {selector}")
            else:
                self.service.wait_for_timeout(timeout)
                return ToolResult(True, f"Waited {timeout}ms")
        except ProviderError as exc:
            return ToolResult(False, str(exc))
