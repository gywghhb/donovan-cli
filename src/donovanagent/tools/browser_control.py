from __future__ import annotations

from donovanagent.tools.base import ToolExecutionContext, ToolResult


def browser_open(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Open a URL in the browser."""
    svc = ctx.browser_service
    if svc is None:
        return ToolResult(False, "Browser service is not available.")
    url = str(args.get("url", ""))
    if not url:
        return ToolResult(False, "Missing 'url' argument.")
    browser_type = str(args.get("browser_type", "auto"))
    cdp_endpoint = str(args.get("cdp_endpoint")) if args.get("cdp_endpoint") else None
    try:
        svc.open(url, browser_type=browser_type, cdp_endpoint=cdp_endpoint)
        return ToolResult(True, f"Opened {url}")
    except Exception as exc:
        return ToolResult(False, f"Failed to open browser: {exc}")


def browser_snapshot(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Get the visible text content of the current page."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        max_chars = int(args.get("max_chars", 5000))
        text = svc.get_text()[:max_chars]
        return ToolResult(True, text)
    except Exception as exc:
        return ToolResult(False, f"Failed to get page text: {exc}")


def browser_screenshot(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Take a screenshot of the current page and return the file path."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        path = str(args.get("path")) if args.get("path") else None
        result_path = svc.screenshot(path=path)
        return ToolResult(True, f"Screenshot saved to: {result_path}")
    except Exception as exc:
        return ToolResult(False, f"Failed to take screenshot: {exc}")


def browser_click(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Click an element on the page identified by a CSS selector."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    selector = str(args.get("selector", ""))
    if not selector:
        return ToolResult(False, "Missing 'selector' argument.")
    try:
        svc.click(selector)
        return ToolResult(True, f"Clicked element: {selector}")
    except Exception as exc:
        return ToolResult(False, f"Failed to click '{selector}': {exc}")


def browser_type(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Type text into an element identified by a CSS selector."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    selector = str(args.get("selector", ""))
    text = str(args.get("text", ""))
    if not selector:
        return ToolResult(False, "Missing 'selector' argument.")
    try:
        svc.type_text(selector, text)
        return ToolResult(True, f"Typed into {selector}")
    except Exception as exc:
        return ToolResult(False, f"Failed to type into '{selector}': {exc}")


def browser_press(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Press a keyboard key (e.g. 'Enter', 'Tab', 'Escape')."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    key = str(args.get("key", ""))
    if not key:
        return ToolResult(False, "Missing 'key' argument.")
    try:
        svc.press(key)
        return ToolResult(True, f"Pressed key: {key}")
    except Exception as exc:
        return ToolResult(False, f"Failed to press '{key}': {exc}")


def browser_extract_links(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Extract all links from the current page."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        links = svc.extract_links()
        if not links:
            return ToolResult(True, "No links found on the page.")
        lines = [f"{i}. {l['text']} -> {l['href']}" for i, l in enumerate(links, 1)]
        return ToolResult(True, "\n".join(lines[:100]))
    except Exception as exc:
        return ToolResult(False, f"Failed to extract links: {exc}")


def browser_current_url(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Get the current URL of the browser page."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        return ToolResult(True, svc.current_url())
    except Exception as exc:
        return ToolResult(False, f"Failed to get URL: {exc}")


def browser_back(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Navigate back in the browser history."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        svc.back()
        return ToolResult(True, "Navigated back.")
    except Exception as exc:
        return ToolResult(False, f"Failed to navigate back: {exc}")


def browser_reload(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Reload the current page."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        svc.reload()
        return ToolResult(True, "Page reloaded.")
    except Exception as exc:
        return ToolResult(False, f"Failed to reload: {exc}")


def browser_get_html(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Get the full HTML content of the current page."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    try:
        max_chars = int(args.get("max_chars", 10000))
        html = svc.get_html()[:max_chars]
        return ToolResult(True, html)
    except Exception as exc:
        return ToolResult(False, f"Failed to get HTML: {exc}")


def browser_close(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Close the browser."""
    svc = ctx.browser_service
    if svc is None:
        return ToolResult(False, "Browser service is not available.")
    try:
        svc.close()
        return ToolResult(True, "Browser closed.")
    except Exception as exc:
        return ToolResult(False, f"Failed to close browser: {exc}")


def browser_evaluate(ctx: ToolExecutionContext, args: dict[str, object]) -> ToolResult:
    """Evaluate JavaScript in the current page context."""
    svc = ctx.browser_service
    if svc is None or not svc.is_open:
        return ToolResult(False, "Browser is not open. Use browser_open first.")
    script = str(args.get("script", ""))
    if not script:
        return ToolResult(False, "Missing 'script' argument.")
    try:
        result = svc.evaluate(script)
        return ToolResult(True, str(result))
    except Exception as exc:
        return ToolResult(False, f"JavaScript evaluation failed: {exc}")
