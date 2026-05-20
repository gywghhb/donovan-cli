from __future__ import annotations

import json
import queue
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MANIFEST = {
    "manifest_version": 3,
    "name": "Donovan Browser Companion",
    "version": "0.1.13",
    "description": "Lets Donovan Agent read and interact with the active browser tab with user permission.",
    "permissions": ["activeTab", "scripting", "tabs"],
    "host_permissions": ["<all_urls>", "http://127.0.0.1:8765/*", "http://localhost:8765/*"],
    "background": {"service_worker": "background.js"},
    "action": {"default_title": "Donovan Companion"},
    "content_scripts": [
        {
            "matches": ["<all_urls>"],
            "js": ["content.js"],
            "run_at": "document_idle",
        }
    ],
}

BACKGROUND_JS = r"""
const SERVER = "http://127.0.0.1:8765";

async function post(path, body) {
  try {
    await fetch(`${SERVER}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {})
    });
  } catch (err) {}
}

async function getCommand() {
  try {
    const response = await fetch(`${SERVER}/extension/poll`);
    if (!response.ok) return null;
    const data = await response.json();
    return data.command || null;
  } catch (err) {
    return null;
  }
}

async function activeTab() {
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  return tabs[0] || null;
}

async function listTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.map((tab, index) => ({
    index,
    id: tab.id,
    title: tab.title || "",
    url: tab.url || "",
    active: !!tab.active,
    windowId: tab.windowId
  }));
}

async function sendToTab(tabId, command) {
  try {
    return await chrome.tabs.sendMessage(tabId, command);
  } catch (err) {
    await chrome.scripting.executeScript({target: {tabId}, files: ["content.js"]});
    return await chrome.tabs.sendMessage(tabId, command);
  }
}

async function runCommand(command) {
  const tab = await activeTab();
  if (!tab && command.type !== "list_tabs") {
    return {success: false, error: "No active tab"};
  }
  if (command.type === "list_tabs") {
    return {success: true, tabs: await listTabs()};
  }
  if (command.type === "use_tab") {
    const tabs = await chrome.tabs.query({});
    const needle = String(command.tab || "").toLowerCase();
    let target = tabs.find((t, i) => String(i) === needle || String(t.id) === needle);
    if (!target) {
      target = tabs.find(t => (t.title || "").toLowerCase().includes(needle) || (t.url || "").toLowerCase().includes(needle));
    }
    if (!target) return {success: false, error: `No tab matched ${command.tab}`};
    await chrome.tabs.update(target.id, {active: true});
    await chrome.windows.update(target.windowId, {focused: true});
    return {success: true, title: target.title || "", url: target.url || ""};
  }
  if (command.type === "active_tab") {
    return {success: true, title: tab.title || "", url: tab.url || "", id: tab.id};
  }
  if (command.type === "screenshot") {
    const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {format: "png"});
    return {success: true, title: tab.title || "", url: tab.url || "", dataUrl};
  }
  const result = await sendToTab(tab.id, command);
  return Object.assign({title: tab.title || "", url: tab.url || ""}, result || {});
}

async function tick() {
  const command = await getCommand();
  if (!command) return;
  let result;
  try {
    result = await runCommand(command);
  } catch (err) {
    result = {success: false, error: String(err && err.message || err)};
  }
  await post("/extension/result", {id: command.id, result});
}

setInterval(tick, 400);
chrome.runtime.onInstalled.addListener(() => tick());
chrome.action.onClicked.addListener(() => tick());
"""

CONTENT_JS = r"""
function nodePath(el) {
  if (!el) return "";
  if (el.id) return `#${CSS.escape(el.id)}`;
  const parts = [];
  while (el && el.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
    let part = el.nodeName.toLowerCase();
    if (el.className && typeof el.className === "string") {
      const cls = el.className.trim().split(/\s+/).slice(0, 2).map(c => `.${CSS.escape(c)}`).join("");
      part += cls;
    }
    parts.unshift(part);
    el = el.parentElement;
  }
  return parts.join(" > ");
}

function snapshot() {
  const interactive = Array.from(document.querySelectorAll("a,button,input,textarea,select,[role='button'],[contenteditable='true']"))
    .slice(0, 120)
    .map((el, index) => ({
      index,
      selector: nodePath(el),
      text: (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "").trim().slice(0, 160),
      tag: el.tagName.toLowerCase()
    }));
  return {
    success: true,
    title: document.title,
    url: location.href,
    text: (document.body ? document.body.innerText : "").slice(0, 20000),
    selection: String(window.getSelection ? window.getSelection() : ""),
    interactive
  };
}

chrome.runtime.onMessage.addListener((command, sender, sendResponse) => {
  (async () => {
    if (command.type === "snapshot") return snapshot();
    if (command.type === "click") {
      const el = document.querySelector(command.selector);
      if (!el) return {success: false, error: `Selector not found: ${command.selector}`};
      el.scrollIntoView({block: "center", inline: "center"});
      el.click();
      return {success: true};
    }
    if (command.type === "type") {
      const el = document.querySelector(command.selector);
      if (!el) return {success: false, error: `Selector not found: ${command.selector}`};
      el.focus();
      if ("value" in el) {
        el.value = command.text || "";
        el.dispatchEvent(new Event("input", {bubbles: true}));
        el.dispatchEvent(new Event("change", {bubbles: true}));
      } else {
        el.textContent = command.text || "";
        el.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: command.text || ""}));
      }
      return {success: true};
    }
    if (command.type === "press") {
      document.activeElement && document.activeElement.dispatchEvent(new KeyboardEvent("keydown", {key: command.key || "Enter", bubbles: true}));
      return {success: true};
    }
    if (command.type === "evaluate") {
      const value = Function(`"use strict"; return (${command.script});`)();
      return {success: true, value: String(value)};
    }
    return {success: false, error: `Unknown command: ${command.type}`};
  })().then(sendResponse).catch(err => sendResponse({success: false, error: String(err && err.message || err)}));
  return true;
});
"""


@dataclass
class CompanionCommand:
    id: str
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    event: threading.Event = field(default_factory=threading.Event)


class BrowserCompanionService:
    def __init__(self, data_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.data_dir = Path(data_dir)
        self.extension_dir = self.data_dir / "browser_companion_extension"
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._queue: "queue.Queue[CompanionCommand]" = queue.Queue()
        self._pending: dict[str, CompanionCommand] = {}
        self._lock = threading.Lock()
        self._last_seen = 0.0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def install_extension_files(self) -> Path:
        self.extension_dir.mkdir(parents=True, exist_ok=True)
        (self.extension_dir / "manifest.json").write_text(json.dumps(MANIFEST, indent=2), encoding="utf-8")
        (self.extension_dir / "background.js").write_text(BACKGROUND_JS, encoding="utf-8")
        (self.extension_dir / "content.js").write_text(CONTENT_JS, encoding="utf-8")
        return self.extension_dir

    def setup_instructions(self) -> str:
        path = self.install_extension_files()
        try:
            webbrowser.open("edge://extensions/")
        except Exception:
            pass
        return (
            f"Extension folder: {path}\n\n"
            "In Microsoft Edge:\n"
            "1. Open edge://extensions/.\n"
            "2. Turn on Developer mode.\n"
            "3. Click Load unpacked.\n"
            "4. Select the extension folder above.\n"
            "5. Run /browser companion start, then use /browser companion active or ask Donovan about the active tab."
        )

    def start(self) -> None:
        if self._server is not None:
            return
        self.install_extension_files()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._send_json(200, {})

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/extension/poll":
                    service._last_seen = __import__("time").time()
                    try:
                        command = service._queue.get_nowait()
                    except queue.Empty:
                        self._send_json(200, {"command": None})
                        return
                    with service._lock:
                        service._pending[command.id] = command
                    self._send_json(200, {"command": command.payload})
                elif self.path == "/status":
                    self._send_json(200, service.status())
                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if self.path == "/extension/result":
                    command_id = str(data.get("id", ""))
                    with service._lock:
                        command = service._pending.pop(command_id, None)
                    if command is not None:
                        command.result = data.get("result") or {}
                        command.event.set()
                    self._send_json(200, {"ok": True})
                else:
                    self._send_json(404, {"error": "not found"})

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def status(self) -> dict[str, Any]:
        import time

        connected = self._last_seen > 0 and (time.time() - self._last_seen) < 5
        return {
            "running": self._server is not None,
            "url": self.url,
            "extension_dir": str(self.extension_dir),
            "extension_connected": connected,
        }

    def command(self, command_type: str, **kwargs: Any) -> dict[str, Any]:
        self.start()
        command_id = str(uuid.uuid4())
        payload = {"id": command_id, "type": command_type, **kwargs}
        command = CompanionCommand(id=command_id, payload=payload)
        self._queue.put(command)
        if not command.event.wait(timeout=10):
            return {"success": False, "error": "Browser companion did not respond. Make sure the extension is installed and enabled."}
        return command.result or {"success": False, "error": "No result from browser companion."}
