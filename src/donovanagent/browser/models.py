from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserSession:
    id: str = ""
    session_id: str | None = None
    browser_type: str = "chromium"  # chromium, chrome, edge, webkit, cdp
    url: str | None = None
    status: str = "closed"  # open, closed
    started_at: str | None = None
    closed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
